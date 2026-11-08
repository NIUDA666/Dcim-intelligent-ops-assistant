"""应用依赖容器：演示环境只创建一套只读适配器与内存服务。"""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.fixtures import FixtureStore, load_fixtures
from app.adapters.mock_knowledge import MockKnowledgeAdapter
from app.adapters.mock_monitoring import MockMonitoringAdapter
from app.adapters.mock_topology import MockTopologyAdapter
from app.graphs.analysis_graph import AnalysisRunner
from app.rag.llm_client import LLMClient
from app.scenarios.models import PublishedBundle
from app.scenarios.registry import (
    ConfigStore,
    runtime_alarm_dictionary,
    runtime_metric_dictionary,
)
from app.services.access import AccessContext, demo_access_context
from app.services.import_service import ImportService
from app.services.plan_compiler import CompileDependencies, PlanCompiler
from app.services.query_service import QueryService
from app.settings import settings
from app.tools.gateway import ToolGateway


@dataclass
class AppContainer:
    store: FixtureStore
    config_store: ConfigStore
    bundle: PublishedBundle
    access: AccessContext
    metric_dictionary: dict
    alarm_dictionary: dict
    monitoring: MockMonitoringAdapter
    topology: MockTopologyAdapter
    knowledge: MockKnowledgeAdapter
    gateway: ToolGateway
    compiler: PlanCompiler
    analysis_runner: AnalysisRunner
    query_service: QueryService
    import_service: ImportService
    llm: LLMClient


def build_container() -> AppContainer:
    store = load_fixtures()
    access = demo_access_context()
    config_store = ConfigStore(settings.published_dir)
    bundle = config_store.load()
    metric_dictionary = runtime_metric_dictionary(bundle)
    alarm_dictionary = runtime_alarm_dictionary(bundle)
    monitoring = MockMonitoringAdapter(store)
    topology = MockTopologyAdapter(store)
    knowledge = MockKnowledgeAdapter(store)
    gateway = ToolGateway(monitoring, topology, knowledge, store)
    llm = LLMClient()
    compiler = PlanCompiler(
        CompileDependencies(
            store=store,
            config_store=config_store,
            monitoring=monitoring,
            access=access,
            metric_dictionary=metric_dictionary,
            alarm_dictionary=alarm_dictionary,
        )
    )
    analysis_runner = AnalysisRunner(
        gateway=gateway,
        store=store,
        metric_dictionary=metric_dictionary,
        llm=llm,
    )
    query_service = QueryService(
        store=store,
        config_store=config_store,
        monitoring=monitoring,
        gateway=gateway,
        compiler=compiler,
        analysis_runner=analysis_runner,
        access=access,
        metric_dictionary=metric_dictionary,
        alarm_dictionary=alarm_dictionary,
        llm=llm,
    )
    return AppContainer(
        store=store,
        config_store=config_store,
        bundle=bundle,
        access=access,
        metric_dictionary=metric_dictionary,
        alarm_dictionary=alarm_dictionary,
        monitoring=monitoring,
        topology=topology,
        knowledge=knowledge,
        gateway=gateway,
        compiler=compiler,
        analysis_runner=analysis_runner,
        query_service=query_service,
        import_service=ImportService(knowledge),
        llm=llm,
    )
