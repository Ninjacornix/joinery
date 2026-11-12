"""Every catalog tool has one module that declares its adapter and readiness checks."""

from __future__ import annotations

import pytest

from joinery import authoring, tools
from joinery.catalog import REMOVED_TOOLS, load_catalog
from joinery.recipe import Recipe, _tool_argv
from joinery.tools.base import (
    AUDACITY_WORKER,
    BLENDER_WORKER,
    CREATIVE_WORKER,
    INKSCAPE_WORKER,
    MCP_WORKER,
    HostExec,
    ImageCheck,
    McpProbe,
    WorkerExec,
)

WORKERS = {CREATIVE_WORKER, MCP_WORKER, BLENDER_WORKER, INKSCAPE_WORKER, AUDACITY_WORKER}


def test_registry_covers_exactly_the_catalog() -> None:
    assert set(tools.REGISTRY) == {tool.id for tool in load_catalog().tool}
    for tool in load_catalog().tool:
        assert tools.get(tool.id).name == tool.name


def test_removed_tools_have_no_module_or_adapter() -> None:
    for removed in REMOVED_TOOLS:
        assert removed not in tools.REGISTRY
        assert removed not in authoring.SUPPORTED
        with pytest.raises(ValueError, match="No tool module"):
            tools.get(removed)


@pytest.mark.parametrize("tool_id", sorted(tools.REGISTRY))
def test_every_module_declares_required_checks_and_its_features(tool_id: str) -> None:
    module = tools.get(tool_id)
    assert any(not check.feature for check in module.checks)
    declared = {feature.id for feature in module.features}
    used = {check.feature for check in module.checks if check.feature}
    assert used == declared
    for check in module.checks:
        if isinstance(check, (ImageCheck, WorkerExec)):
            assert check.image in WORKERS
        if isinstance(check, (WorkerExec, HostExec)) and check.version:
            assert "(" in check.version
        if isinstance(check, McpProbe) and check.image:
            assert check.image in WORKERS


def test_worker_tools_check_their_image_before_running_in_it() -> None:
    for module in tools.REGISTRY.values():
        images = [c.image for c in module.checks if isinstance(c, ImageCheck)]
        for check in module.checks:
            if isinstance(check, WorkerExec):
                assert check.image in images, (module.id, check.argv)


@pytest.mark.parametrize("tool_id", sorted(tools.REGISTRY))
def test_builtin_server_matches_module_family(tool_id: str) -> None:
    builtin = load_catalog().get(tool_id).builtin_server
    if builtin:
        assert tools.get(tool_id).family == builtin
    family = tools.get(tool_id).family
    assert family == "" or family in tools.FAMILY_PACKAGES


@pytest.mark.parametrize(
    "tool_id",
    sorted(t for t, m in tools.REGISTRY.items() if m.script_ext or m.authoring),
)
def test_module_replay_matches_recipe_replay(tool_id: str) -> None:
    # Recipe replay dispatches through the module, bound and all.
    recipe = Recipe(
        tool=tool_id,
        script="recipes/x" + (tools.get(tool_id).script_ext or ".py"),
        outputs=("art/x",),
    )
    argv = tools.get(tool_id).replay_argv(recipe.script)
    assert _tool_argv(recipe) == argv
    assert any(recipe.script in part for part in argv)


def test_authoring_tables_come_from_the_modules() -> None:
    assert authoring.SUPPORTED == tools.authoring_ids()
    assert authoring.SUPPORTED == {
        "blender",
        "krita",
        "gimp",
        "inkscape",
        "material-maker",
        "supercollider",
        "surge-xt",
        "audacity",
    }
    assert authoring.SUFFIXES["krita"] == ".kra"
    assert authoring.SUFFIXES["audacity"] == ".aup3"
    assert authoring.SUFFIXES["surge-xt"] == ".json"


def test_family_maps_follow_the_registry() -> None:
    assert tools.script_tools("joinery-paint") == {"krita", "gimp"}
    assert tools.script_tools("joinery-sound") == {"supercollider", "surge-xt", "audacity"}
    assert tools.script_tools("joinery-materialmaker") == set()
    assert tools.families_for({"surge-xt"}) == ["joinery-sound"]
    assert tools.families_for({"gimp", "blender"}) == [
        "joinery-blender-ops",
        "joinery-paint",
    ]
    programs = {
        key: value
        for module in tools.family_tools("joinery-sound")
        for key, value in module.capability_programs().items()
    }
    # Audacity has its own worker, which readiness checks instead.
    assert programs == {"supercollider": "sclang", "surge-xt": "joinery-surge"}


def test_gimp_edits_xcf_through_its_python_fu_adapter() -> None:
    gimp = tools.get("gimp")
    assert gimp.authoring and gimp.script_ext == ".py"
    assert authoring.SUFFIXES["gimp"] == ".xcf"
