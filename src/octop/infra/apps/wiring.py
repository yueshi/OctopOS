"""Composition root for :class:`AppService` instances.

The service needs the same repositories and workspace resolver everywhere it
is built (HTTP routers, agent tools, the source scheduler). Keeping the
construction in one place means a new dependency is added once, not in every
caller.
"""

from __future__ import annotations

from octop.infra.apps.ingress import AppIngressService
from octop.infra.apps.service import AppService, ExternalToolRunner
from octop.infra.apps.webhooks import AppWebhookService
from octop.infra.db.services import RepoBundle


def build_app_service(
    *,
    repos: RepoBundle,
    workspaces: object,
    external_tool_runner: ExternalToolRunner | None = None,
) -> AppService:
    return AppService(
        app_repo=repos.light_app_repo,
        agent_repo=repos.agent_repo,
        user_repo=repos.user_repo,
        workspaces=workspaces,  # type: ignore[arg-type]
        secret_repo=repos.secret_repo,
        audit_repo=repos.audit_repo,
        access_repo=repos.app_access_repo,
        access_code_repo=repos.app_access_code_repo,
        binding_repo=repos.app_binding_repo,
        service_repo=repos.app_service_repo,
        connector_repo=repos.connector_repo,
        event_repo=repos.app_event_repo,
        schedule_repo=repos.app_schedule_repo,
        webhook_repo=repos.app_webhook_repo,
        ingress_repo=repos.app_ingress_repo,
        external_source_repo=repos.external_source_repo,
        external_subscription_repo=repos.external_subscription_repo,
        setup_plan_repo=repos.app_setup_plan_repo,
        external_tool_runner=external_tool_runner,
    )


def build_webhook_service(
    *,
    repos: RepoBundle,
    app_service: AppService,
) -> AppWebhookService:
    return AppWebhookService(
        app_service=app_service,
        webhook_repo=repos.app_webhook_repo,
        source_repo=repos.external_source_repo,
        subscription_repo=repos.external_subscription_repo,
        secret_repo=repos.secret_repo,
    )


def build_ingress_service(
    *,
    repos: RepoBundle,
    app_service: AppService,
) -> AppIngressService:
    return AppIngressService(
        app_service=app_service,
        app_repo=repos.light_app_repo,
        ingress_repo=repos.app_ingress_repo,
        source_repo=repos.external_source_repo,
        subscription_repo=repos.external_subscription_repo,
        secret_repo=repos.secret_repo,
    )


__all__ = ["build_app_service", "build_ingress_service", "build_webhook_service"]
