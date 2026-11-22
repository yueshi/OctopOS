"""Action declared by every MiniApp HTTP route.

A ``None`` action means the route is not app-ACL scoped: it lists apps, works
on agent-level setup plans/templates, or creates/restores an app under an
agent. Those handlers keep using agent/user access checks. Every route that
does touch one app must declare an ``Action`` so a new endpoint cannot silently
fall back to an ad-hoc permission check.
"""

from __future__ import annotations

from octop.infra.apps.access import Action

APP_ROUTE_ACTIONS: dict[str, Action | None] = {
    # App catalog / templates / setup plans / app creation (agent scoped).
    "list_apps": None,
    "list_in_thread_apps": None,
    "list_app_templates": None,
    "get_app_template_preview": None,
    "create_app_from_template": None,
    "quick_inspect_app": None,
    "quick_create_app": None,
    "list_setup_plans": None,
    "get_setup_plan": None,
    "confirm_setup_plan": None,
    "execute_setup_plan": None,
    "cancel_setup_plan": None,
    "import_app_dataset": None,
    "preflight_restore_app": None,
    "restore_app": None,
    # App metadata / read-only surfaces.
    "get_app": Action.VIEW_APP,
    "list_app_tables": Action.VIEW_APP,
    "get_app_data_version": Action.VIEW_APP,
    "get_app_html": Action.VIEW_APP,
    "report_security_event": Action.VIEW_APP,
    "list_app_files": Action.VIEW_APP,
    "list_app_assets": Action.VIEW_APP,
    "get_app_asset": Action.VIEW_APP,
    "get_app_preview": Action.VIEW_APP,
    "get_app_dataset": Action.VIEW_APP,
    # Rows.
    "query_app_dataset": Action.VIEW_ROW,
    "get_app_rows": Action.VIEW_ROW,
    "append_app_row": Action.CREATE_ROW,
    "update_app_row": Action.UPDATE_ROW,
    "delete_app_row": Action.DELETE_ROW,
    "get_app_row_history": Action.VIEW_HISTORY,
    "aggregate_app_rows": Action.AGGREGATE,
    "export_app_table": Action.EXPORT,
    "export_app_dataset": Action.EXPORT_DATASET,
    "upload_app_import_file": Action.UPLOAD_IMPORT,
    "import_app_table": Action.CREATE_ROW,
    # Schema / structure.
    "set_app_table_visibility": Action.MANAGE_APP_SETTINGS,
    "set_app_table_rules": Action.MANAGE_APP_SETTINGS,
    "alter_app_schema": Action.MANAGE_STRUCTURE,
    "update_app": Action.MANAGE_STRUCTURE,
    "update_app_capabilities": Action.MANAGE_APP_SETTINGS,
    "restore_app_html": Action.MANAGE_STRUCTURE,
    "upload_app_asset": Action.MANAGE_ASSETS,
    # Services / bindings / events.
    "list_app_bindings": Action.VIEW_APP,
    "save_app_binding": Action.MANAGE_BINDINGS,
    "delete_app_binding": Action.MANAGE_BINDINGS,
    "list_app_services": Action.VIEW_APP,
    "save_app_service": Action.MANAGE_BINDINGS,
    "delete_app_service": Action.MANAGE_BINDINGS,
    "call_app_service": Action.SERVICE_CALL,
    "fetch_app_service_image": Action.SERVICE_CALL,
    "list_app_events": Action.VIEW_APP,
    "ack_app_events": Action.VIEW_APP,
    "emit_app_event": Action.EMIT_EVENT,
    # Webhooks / ingress / sources.
    "list_app_webhooks": Action.VIEW_APP,
    "create_app_webhook": Action.MANAGE_INGRESS,
    "update_app_webhook": Action.MANAGE_INGRESS,
    "delete_app_webhook": Action.MANAGE_INGRESS,
    "list_app_ingress": Action.VIEW_APP,
    "create_app_ingress": Action.MANAGE_INGRESS,
    "update_app_ingress": Action.MANAGE_INGRESS,
    "delete_app_ingress": Action.MANAGE_INGRESS,
    "rotate_app_ingress_secret": Action.MANAGE_INGRESS,
    "test_app_ingress": Action.MANAGE_INGRESS,
    "list_app_sources": Action.VIEW_APP,
    "save_app_source": Action.MANAGE_STRUCTURE,
    "delete_app_source": Action.MANAGE_STRUCTURE,
    "run_app_source": Action.RUN_SOURCE,
    "list_app_source_tools": Action.MANAGE_STRUCTURE,
    # Schedules.
    "list_app_schedules": Action.VIEW_APP,
    "save_app_schedule": Action.MANAGE_SCHEDULE,
    "update_app_schedule": Action.MANAGE_SCHEDULE,
    "delete_app_schedule": Action.MANAGE_SCHEDULE,
    # Sharing / publication / lifecycle.
    "list_app_shares": Action.MANAGE_SHARES,
    "set_app_shares": Action.MANAGE_SHARES,
    "get_app_publication": Action.VIEW_APP,
    "create_app_publication": Action.PUBLISH,
    "publish_app_publication": Action.PUBLISH,
    "revoke_app_publication": Action.PUBLISH,
    "transfer_app": Action.MANAGE_MEMBERS,
    "delete_app": Action.DELETE_APP,
    "backup_app": Action.MANAGE_STRUCTURE,
    # Assistant / generation / games.
    "get_app_assistant_history": Action.AI,
    "ask_app_assistant": Action.AI,
    "generate_app_text": Action.AI,
    "create_app_game_session": Action.RUN_GAME,
    # Members / external invites / join codes.
    "list_app_members": Action.VIEW_APP,
    "add_app_member": Action.MANAGE_MEMBERS,
    "add_app_guest": Action.MANAGE_MEMBERS,
    "update_app_member": Action.MANAGE_MEMBERS,
    "remove_app_member": Action.MANAGE_MEMBERS,
    "list_app_invites": Action.MANAGE_MEMBERS,
    "create_app_invite": Action.MANAGE_MEMBERS,
    "revoke_app_invite": Action.MANAGE_MEMBERS,
    "update_app_invite_host": Action.MANAGE_MEMBERS,
    "preview_app_invite": None,
    "redeem_app_invite": None,
    "list_app_join_codes": Action.MANAGE_MEMBERS,
    "create_app_join_code": Action.MANAGE_MEMBERS,
    "revoke_app_join_code": Action.MANAGE_MEMBERS,
    "preview_app_join_code": None,
    "redeem_app_join_code": None,
    # Guest portal / scoped sessions.
    "create_app_session": Action.VIEW_APP,
    "create_guest_app_session": None,
    "refresh_guest_session": None,
    "revoke_guest_session": None,
    "list_guest_apps": None,
    "leave_guest_app": None,
    "delete_guest_account": None,
}

__all__ = ["APP_ROUTE_ACTIONS"]
