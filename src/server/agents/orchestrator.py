import json
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from src.server.agents.fundamental_analysis import FundamentalAnalysisAgent
from src.server.agents.market_sentiment import MarketSentimentAgent
from src.server.agents.report_verification import ReportVerificationAgent
from src.server.agents.research import ResearchAgent
from src.server.agents.scenario_scoring import ScenarioScoringAgent
from src.server.models.intent import ResearchIntent
from src.server.models.request import ResearchRequest
from src.server.models.response import ResearchResponse
from src.server.services.openrouter import OpenRouterClient


class OrchestratorAgent:
    def __init__(self, max_retries: int = 2, llm_client: OpenRouterClient | None = None) -> None:
        self.max_retries = max_retries
        self.llm_client = llm_client or OpenRouterClient()

    def run(self, request: ResearchRequest) -> ResearchResponse:
        response: ResearchResponse | None = None
        for event in self.run_stream(request):
            if event["type"] == "final":
                response = event["payload"]
        if response is None:
            raise RuntimeError("Orchestrator finished without final response.")
        return response

    def run_stream(self, request: ResearchRequest) -> Generator[dict, None, None]:
        agent_statuses = self._initial_agent_statuses()
        research_state: dict = {
            "intent": None,
            "evidence": [],
            "normalized_data": {},
            "fundamental_analysis": {},
            "market_sentiment": {},
            "scenarios": [],
            "open_questions": [],
            "validation_result": {},
        }
        yield {"type": "agent_status", "payload": agent_statuses}

        intent = self._parse_intent(request.query)
        research_state["intent"] = intent.model_dump()
        self._set_agent_status(
            agent_statuses,
            "orchestrator",
            status="completed",
            action="initialized research state",
            details=[f"intent={intent.intent}", f"scope={intent.scope}"],
        )
        yield {"type": "agent_status", "payload": agent_statuses}
        yield {"type": "state_update", "payload": {"research_state": research_state}}

        research_result = self._run_research_with_status(
            agent_statuses=agent_statuses,
            query=request.query,
            intent=intent,
            open_questions=[],
            pass_id=0,
            action="collecting evidence and normalizing data",
        )
        yield {"type": "agent_status", "payload": agent_statuses}
        self._write_research_state(research_state, research_result)
        yield {"type": "state_update", "payload": {"research_state": research_state}}

        fundamental_analysis, market_sentiment = self._run_parallel_analysis(
            agent_statuses=agent_statuses,
            research_result=research_result,
        )
        yield {"type": "agent_status", "payload": agent_statuses}
        research_state["fundamental_analysis"] = fundamental_analysis
        research_state["market_sentiment"] = market_sentiment

        open_questions = self._collect_open_questions(intent, fundamental_analysis, market_sentiment)
        research_state["open_questions"] = open_questions
        yield {"type": "state_update", "payload": {"research_state": research_state}}

        # If analysis detects gaps, trigger supplementary research and re-run analysis.
        if open_questions:
            research_result = self._run_research_with_status(
                agent_statuses=agent_statuses,
                query=request.query,
                intent=intent,
                open_questions=open_questions,
                pass_id=1,
                action="supplementary evidence collection from open questions",
            )
            yield {"type": "agent_status", "payload": agent_statuses}
            self._write_research_state(research_state, research_result)
            yield {"type": "state_update", "payload": {"research_state": research_state}}

            fundamental_analysis, market_sentiment = self._run_parallel_analysis(
                agent_statuses=agent_statuses,
                research_result=research_result,
            )
            yield {"type": "agent_status", "payload": agent_statuses}
            research_state["fundamental_analysis"] = fundamental_analysis
            research_state["market_sentiment"] = market_sentiment
            research_state["open_questions"] = self._collect_open_questions(
                intent,
                fundamental_analysis,
                market_sentiment,
            )
            yield {"type": "state_update", "payload": {"research_state": research_state}}

        self._set_agent_status(
            agent_statuses,
            "scenario_scoring",
            status="running",
            action="scoring scenarios and normalizing probabilities",
        )
        yield {"type": "agent_status", "payload": agent_statuses}
        scenarios = ScenarioScoringAgent().run(research_result, fundamental_analysis, market_sentiment)
        research_state["scenarios"] = [scenario.model_dump() for scenario in scenarios]
        self._set_agent_status(
            agent_statuses,
            "scenario_scoring",
            status="completed",
            action="scenario probabilities ready",
            details=[f"scenario_count={len(scenarios)}"],
        )
        yield {"type": "agent_status", "payload": agent_statuses}
        yield {"type": "state_update", "payload": {"research_state": research_state}}

        self._set_agent_status(
            agent_statuses,
            "report_verification",
            status="running",
            action="drafting report and running validation checks",
        )
        yield {"type": "agent_status", "payload": agent_statuses}
        response = ReportVerificationAgent().run(
            intent,
            research_result,
            fundamental_analysis,
            market_sentiment,
            scenarios,
            agent_statuses=agent_statuses,
        )
        self._set_agent_status(
            agent_statuses,
            "report_verification",
            status="completed",
            action="report published",
            details=[
                f"is_valid={response.validation_result.is_valid}",
                f"errors={len(response.validation_result.errors)}",
            ],
        )
        response.agent_statuses = agent_statuses
        research_state["validation_result"] = response.validation_result.model_dump()
        yield {"type": "agent_status", "payload": agent_statuses}
        yield {"type": "state_update", "payload": {"research_state": research_state}}
        yield {"type": "final", "payload": response}

    def _run_with_retry(self, operation: Callable[[], object], *, agent: str, action: str) -> object:
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return operation()
            except Exception as exc:  # pragma: no cover - fallback path
                last_error = exc
                if attempt == self.max_retries:
                    break
        assert last_error is not None
        raise RuntimeError(f"{agent} failed while {action}: {last_error}") from last_error

    def _run_research_with_status(
        self,
        *,
        agent_statuses: list[dict],
        query: str,
        intent: ResearchIntent,
        open_questions: list[str],
        pass_id: int,
        action: str,
    ):
        self._set_agent_status(
            agent_statuses,
            "research",
            status="running",
            action=action,
            details=[f"retry_policy=max_{self.max_retries}"],
        )
        result = self._run_with_retry(
            lambda: ResearchAgent().run(
                query,
                intent,
                open_questions=open_questions,
                pass_id=pass_id,
            ),
            agent="research",
            action=action,
        )
        self._set_agent_status(
            agent_statuses,
            "research",
            status="completed",
            action="evidence and normalized data ready",
            details=[f"evidence_count={len(result.evidence)}", f"pass_id={pass_id}"],
        )
        return result

    def _run_parallel_analysis(self, *, agent_statuses: list[dict], research_result):
        self._set_agent_status(
            agent_statuses,
            "fundamental_analysis",
            status="running",
            action="producing fundamentals, valuation, and business risk analysis",
        )
        self._set_agent_status(
            agent_statuses,
            "market_sentiment",
            status="running",
            action="analyzing news, price action, and market narrative",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            fundamental_future = executor.submit(
                self._run_with_retry,
                lambda: FundamentalAnalysisAgent().run(research_result),
                agent="fundamental_analysis",
                action="running fundamental analysis",
            )
            sentiment_future = executor.submit(
                self._run_with_retry,
                lambda: MarketSentimentAgent().run(research_result),
                agent="market_sentiment",
                action="running market sentiment analysis",
            )
            fundamental_analysis = fundamental_future.result()
            market_sentiment = sentiment_future.result()

        self._set_agent_status(
            agent_statuses,
            "fundamental_analysis",
            status="completed",
            action="fundamental analysis ready",
            details=[f"claims={len(fundamental_analysis.get('claims', []))}"],
        )
        self._set_agent_status(
            agent_statuses,
            "market_sentiment",
            status="completed",
            action="market sentiment ready",
            details=[f"claims={len(market_sentiment.get('claims', []))}"],
        )
        return fundamental_analysis, market_sentiment

    def _write_research_state(self, research_state: dict, research_result) -> None:
        research_state["evidence"] = [item.model_dump() for item in research_result.evidence]
        research_state["normalized_data"] = deepcopy(research_result.normalized_data)

    def _collect_open_questions(
        self,
        intent: ResearchIntent,
        fundamental_analysis: dict,
        market_sentiment: dict,
    ) -> list[str]:
        open_questions: list[str] = []
        if not intent.ticker:
            open_questions.append("Need clearer company/ticker mapping from query context")
        if not intent.time_horizon:
            open_questions.append("Need explicit investment horizon to refine scenario assumptions")
        if fundamental_analysis.get("missing_fields"):
            open_questions.append(
                "Need additional data for missing fundamental fields: "
                + ", ".join(fundamental_analysis["missing_fields"])
            )
        if market_sentiment.get("missing_fields"):
            open_questions.append(
                "Need additional sentiment evidence for: "
                + ", ".join(market_sentiment["missing_fields"])
            )
        return open_questions

    def _initial_agent_statuses(self) -> list[dict]:
        return [
            {"agent": "orchestrator", "status": "running", "action": "parsing query", "details": []},
            {"agent": "research", "status": "idle", "action": "waiting", "details": []},
            {"agent": "fundamental_analysis", "status": "idle", "action": "waiting", "details": []},
            {"agent": "market_sentiment", "status": "idle", "action": "waiting", "details": []},
            {"agent": "scenario_scoring", "status": "idle", "action": "waiting", "details": []},
            {"agent": "report_verification", "status": "idle", "action": "waiting", "details": []},
        ]

    def _set_agent_status(
        self,
        statuses: list[dict],
        agent: str,
        *,
        status: str,
        action: str,
        details: list[str] | None = None,
    ) -> None:
        for item in statuses:
            if item["agent"] == agent:
                item["status"] = status
                item["action"] = action
                if details is not None:
                    item["details"] = details
                return

    def _parse_intent(self, query: str) -> ResearchIntent:
        prompt = (
            "You are an investment research intent parser. "
            "Extract structured intent from the user query and return JSON only.\n"
            "Output schema:\n"
            "{\n"
            '  "intent": "investment_research|comparison|scenario_analysis|risk_review|valuation_check|market_event_analysis",\n'
            '  "subjects": ["..."],\n'
            '  "scope": "company|sector|theme|macro|event|mixed",\n'
            '  "ticker": "string|null",\n'
            '  "time_horizon": "string|null",\n'
            '  "risk_level": "low|medium|high|null",\n'
            '  "required_outputs": ["valuation","risks","scenarios"]\n'
            "}\n"
            f"Query: {query}"
        )

        try:
            raw = self.llm_client.complete(prompt)
            parsed = json.loads(raw)
            return ResearchIntent(
                intent=parsed.get("intent", "investment_research"),
                subjects=parsed.get("subjects") or [query],
                scope=parsed.get("scope", "theme"),
                ticker=parsed.get("ticker"),
                risk_level=parsed.get("risk_level"),
                time_horizon=parsed.get("time_horizon"),
                required_outputs=parsed.get("required_outputs") or ["valuation", "risks", "scenarios"],
            )
        except Exception:
            # Minimal fallback if LLM parsing is unavailable.
            return ResearchIntent(
                intent="investment_research",
                subjects=[query],
                scope="theme",
                ticker=None,
                risk_level=None,
                time_horizon=None,
                required_outputs=["valuation", "risks", "scenarios"],
            )
