"""Role assembly is explicit, isolated, and never fabricates unavailable services."""
from dataclasses import replace
from pathlib import Path
import sys
import types

import pytest
from pydantic import ValidationError

from backend.agent_framework.adapters.synthetic import SyntheticAdapters, make_agents
from backend.agent_framework.adapters.templates import QwenAdapterStub
from backend.agent_framework.composition import (
    ROLE_PORTS, RoleBinding, TeamConfiguration, build_agents, build_role,
    describe_agents, describe_configuration, load_config,
)
from backend.agent_framework.ports import AdapterFailure
from backend.agent_framework.validation import ReferenceValidator

ROOT = Path(__file__).resolve().parents[2]


def configuration(**roles):
    values = {role: {"kind": "unconfigured"} for role in ROLE_PORTS}
    values.update(roles)
    return TeamConfiguration(roles=values)


def register(monkeypatch, name, factory):
    module = types.ModuleType(name)
    module.make = factory
    monkeypatch.setitem(sys.modules, name, module)
    return name + ":make"


@pytest.mark.parametrize("change", [
    lambda d: d["roles"].pop("A07"),
    lambda d: d["roles"].update({"A08": {"kind": "unconfigured"}}),
    lambda d: d.update({"profile": "production"}),
    lambda d: d.update({"contract_version": "2.0.0"}),
    lambda d: d.update({"database_url": "not-a-config-field"}),
    lambda d: d["roles"].update({"A02": {"kind": "reference"}}),
])
def test_configuration_requires_complete_supported_development_contract(change):
    data = configuration().model_dump(mode="json")
    change(data)
    with pytest.raises(ValidationError):
        TeamConfiguration.model_validate(data)


@pytest.mark.parametrize("binding", [
    {"kind": "factory"},
    {"kind": "reference", "factory": "team:make"},
    {"kind": "factory", "factory": "team:make", "scenario": "pass"},
    {"kind": "unconfigured", "scenario": "pass"},
    {"kind": "synthetic", "scenario": "unknown"},
    {"kind": "synthetic", "extra": "not-supported"},
])
def test_binding_rejects_options_from_other_modes(binding):
    with pytest.raises(ValidationError):
        RoleBinding.model_validate(binding)


@pytest.mark.parametrize("reference", ["/tmp/file.py:make", "C:\\code\\file.py:make", "team:make()", "team:object.make", "team;exec:make", "team:make\n", "team:함수"])
def test_factory_name_is_a_strict_import_reference(reference):
    with pytest.raises(ValidationError):
        RoleBinding(kind="factory", factory=reference)


def test_load_and_describe_do_not_import_factories(tmp_path, monkeypatch):
    def forbidden(_name):
        pytest.fail("configuration inspection must not import developer code")

    monkeypatch.setattr("backend.agent_framework.composition.importlib.import_module", forbidden)
    config = configuration(A07={"kind": "factory", "factory": "not_installed.reporting:make"})
    path = tmp_path / "팀 설정.json"
    path.write_text(config.model_dump_json(), encoding="utf-8-sig")
    description = describe_configuration(load_config(path))
    assert description["roles"]["A07"] == {"kind": "factory", "factory": "not_installed.reporting:make", "connection_verified": False}
    assert not description["connection_verified"]
    assert set(description["roles"]) == set(ROLE_PORTS)


@pytest.mark.parametrize("content", [
    '{"roles":{},"roles":{}}',
    '{"roles":{"A02":{"kind":"synthetic","kind":"factory"}}}',
    '{"roles":{"A02":{},"A02":{}}}',
    '{"roles":NaN}',
])
def test_load_rejects_ambiguous_json_before_assembly(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_selected_role_is_the_only_factory_imported_and_initialized(monkeypatch):
    called = []
    own = register(monkeypatch, "team_selected", lambda: (called.append("A07") or SyntheticAdapters()))
    config = configuration(A02={"kind": "factory", "factory": "missing.other_team:make"},
                           A07={"kind": "factory", "factory": own},
                           A04_A05={"kind": "factory", "factory": "missing.inspection:make"})
    adapter = build_role(config, "A07")
    assert type(adapter) is SyntheticAdapters
    assert called == ["A07"]


@pytest.mark.parametrize("role", ROLE_PORTS)
def test_every_unconfigured_role_fails_without_substitution(role):
    adapter = build_role(configuration(), role)
    with pytest.raises(AdapterFailure) as caught:
        getattr(adapter, ROLE_PORTS[role][1])(None)
    assert caught.value.code == "ADAPTER_NOT_CONFIGURED"
    assert not caught.value.retryable


def test_build_agents_honors_all_roles_and_reports_mixed_types(monkeypatch):
    class Reporter:
        def report(self, request):
            raise AdapterFailure("NOT_READY", "A07 is intentionally incomplete.")

    config = configuration(A02={"kind": "synthetic"},
                           A07={"kind": "factory", "factory": register(monkeypatch, "team_mixed", Reporter)},
                           A06={"kind": "reference"})
    agents = build_agents(config)
    assert type(agents.extractor) is SyntheticAdapters
    assert type(agents.validator) is ReferenceValidator
    assert type(agents.reporter) is Reporter
    description = describe_agents(agents)
    assert {role: value["kind"] for role, value in description.items()} == {
        "A02": "synthetic", "A07": "custom", "A04_A05": "unconfigured", "A06": "reference",
    }
    assert all(not item["connection_verified"] for item in description.values())
    with pytest.raises(AdapterFailure, match="intentionally incomplete"):
        agents.reporter.report(None)


def test_describe_agents_uses_exact_types_not_names_or_inherited_claims():
    class UserSubclass(SyntheticAdapters):
        pass

    agents = replace(make_agents(), extractor=UserSubclass(), reporter=QwenAdapterStub())
    description = describe_agents(agents)
    assert description["A02"]["kind"] == "custom"
    assert description["A04_A05"]["kind"] == "synthetic"
    assert description["A06"]["kind"] == "reference"
    assert description["A07"]["kind"] == "unconfigured"


@pytest.mark.parametrize("factory", [
    lambda: None,
    lambda: {},
    lambda: SyntheticAdapters,
    lambda: make_agents(),
    lambda: types.SimpleNamespace(extract=lambda request, another_required: None),
])
def test_factory_must_return_instance_with_the_selected_role_method(monkeypatch, factory):
    config = configuration(A02={"kind": "factory", "factory": register(monkeypatch, "team_invalid", factory)})
    with pytest.raises(AdapterFailure) as caught:
        build_role(config, "A02")
    assert caught.value.code == "INVALID_ROLE_ADAPTER"


def test_factory_method_for_a_different_role_is_rejected(monkeypatch):
    factory = lambda: types.SimpleNamespace(report=lambda request: None)
    config = configuration(A02={"kind": "factory", "factory": register(monkeypatch, "team_wrong_role", factory)})
    with pytest.raises(AdapterFailure) as caught:
        build_role(config, "A02")
    assert caught.value.code == "INVALID_ROLE_ADAPTER"


def test_async_factory_and_async_method_are_rejected_without_execution(monkeypatch):
    called = []

    async def async_factory():
        called.append("factory")

    class AsyncAdapter:
        async def extract(self, request):
            called.append("method")

    for name, factory, code in (("team_async_factory", async_factory, "INVALID_FACTORY"),
                                ("team_async_method", AsyncAdapter, "INVALID_ROLE_ADAPTER")):
        config = configuration(A02={"kind": "factory", "factory": register(monkeypatch, name, factory)})
        with pytest.raises(AdapterFailure) as caught:
            build_role(config, "A02")
        assert caught.value.code == code
    assert not called


def test_required_factory_arguments_and_noncallables_are_rejected(monkeypatch):
    for name, factory in (("team_requires_args", lambda value: SyntheticAdapters()), ("team_constant", 42)):
        config = configuration(A02={"kind": "factory", "factory": register(monkeypatch, name, factory)})
        with pytest.raises(AdapterFailure) as caught:
            build_role(config, "A02")
        assert caught.value.code == "INVALID_FACTORY"


def test_factory_failure_is_safe_and_does_not_fall_back(monkeypatch):
    def fail():
        raise RuntimeError("secret-provider-token")

    config = configuration(A07={"kind": "factory", "factory": register(monkeypatch, "team_failed", fail)})
    with pytest.raises(AdapterFailure) as caught:
        build_agents(config)
    assert caught.value.code == "ADAPTER_FACTORY_FAILED"
    assert "secret-provider-token" not in str(caught.value)


def test_missing_factory_module_is_an_error_not_a_fixture():
    config = configuration(A07={"kind": "factory", "factory": "missing_team_module_for_framework_test:make"})
    with pytest.raises(AdapterFailure) as caught:
        build_role(config, "A07")
    assert caught.value.code == "ADAPTER_FACTORY_FAILED"


def test_mutated_or_constructed_config_is_revalidated_before_any_import(monkeypatch):
    imports = []
    monkeypatch.setattr("backend.agent_framework.composition.importlib.import_module", lambda name: imports.append(name))
    config = configuration(A02={"kind": "factory", "factory": "team:make"})
    config.roles.pop("A07")
    with pytest.raises(ValidationError):
        build_role(config, "A02")
    invalid = configuration().model_copy(update={"profile": "production"})
    with pytest.raises(ValidationError):
        build_agents(invalid)
    assert not imports


def test_examples_are_valid_and_reference_validator_keeps_fixture_provenance():
    from backend.agent_framework.adapters.generate_examples import role_examples

    synthetic = load_config(ROOT / "examples/agent_framework/team_config.synthetic.json")
    template = load_config(ROOT / "examples/agent_framework/team_config.template.json")
    assert describe_configuration(template)["roles"]["A07"]["kind"] == "unconfigured"
    assert describe_agents(build_agents(synthetic))["A06"]["kind"] == "reference"
    config = configuration(A06={"kind": "synthetic"})
    request, _expected = role_examples()["A06"]
    result = build_role(config, "A06").validate(request)
    assert result.synthetic
    assert result.verdict == "pass"
