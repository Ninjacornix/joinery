"""Audited read operations shared by the gateway and specialist policy hook."""

GODOT_READS = {
    "get_godot_version",
    "get_project_info",
    "list_project_files",
    "read_file",
    "read_scene",
    "read_project_settings",
    "get_uid",
    "validate_script",
    "validate_scripts",
}
BLENDER_READS = {
    "get_blendfile_summary_datablocks",
    "get_objects_summary",
    "get_blendfile_summary_missing_files",
    "get_blendfile_summary_linked_libraries",
    "get_blendfile_summary_paths",
    "get_python_api_docs",
    "search_api_docs",
    "search_manual_docs",
}
FIRST_PARTY_READS = {
    "document_capabilities",
    "inspect_document",
    "validate_project",
    "transaction_status",
    "kit_list",
    "audit_animations",
}


def read_only(server: str, tool: str) -> bool:
    return (
        (server == "godot" and tool in GODOT_READS)
        or (server == "blender" and tool in BLENDER_READS)
        or (
            server == "joinery-registry"
            and tool
            in {
                "art_direction",
                "game_brief",
                "game_setup",
                "list_assets",
                "resolve_source",
                "kit_list",
            }
        )
        or (server == "claude-collider" and tool == "cc_status")
        or (
            server
            in {
                "joinery-blender-ops",
                "joinery-paint",
                "joinery-sound",
                "joinery-materialmaker",
                "joinery-verify",
            }
            and tool in FIRST_PARTY_READS
        )
    )
