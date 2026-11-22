import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { appsApi, type AppMemberPermission } from "../../api/modules/apps";
import {
  getAuthToken,
  hasGuestSession,
  setAppToken,
  setGuestSession,
} from "../../api/request";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import { message } from "../../utils/antdMessage";

const { Text, Title } = Typography;

interface Preview {
  appId: string;
  appName: string;
  audience: "guest" | "member";
  role: AppMemberPermission;
  requiresApproval: boolean;
}

function roleText(role: AppMemberPermission, t: (key: string) => string) {
  if (role === "participate") return t("apps.memberParticipate");
  if (role === "view") return t("apps.permissionView");
  if (role === "edit_data") return t("apps.permissionEditData");
  return t("apps.permissionEditStructure");
}

export default function JoinPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const inviteToken = searchParams.get("invite") || "";
  const joinCode = searchParams.get("code") || "";
  const [manualCode, setManualCode] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [loading, setLoading] = useState(Boolean(inviteToken || joinCode));
  const [error, setError] = useState("");
  const [hostPending, setHostPending] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [consent, setConsent] = useState(false);
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(false);

  const mode = useMemo(
    () => (inviteToken ? "invite" : joinCode ? "join" : ""),
    [inviteToken, joinCode],
  );
  const pendingGuest = !getAuthToken() && hasGuestSession();

  const loadPreview = useCallback(async () => {
    if (!mode) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    setHostPending(false);
    try {
      if (mode === "invite") {
        const data = await appsApi.previewInvite(inviteToken);
        setPreview({
          appId: data.app_id,
          appName: data.app_name,
          audience: data.audience,
          role: data.permission,
          requiresApproval: false,
        });
      } else {
        const data = await appsApi.previewJoinCode(joinCode);
        setPreview({
          appId: data.app_id,
          appName: data.app_name,
          audience: data.audience,
          role: data.role,
          // Host approval is decided at redemption; a pending result still
          // creates the membership, so it must not block this form.
          requiresApproval:
            data.requires_approval || data.host_approval_required,
        });
      }
    } catch (err) {
      if (parseApiError(err)?.code === "APP_HOST_APPROVAL_REQUIRED") {
        setHostPending(true);
      } else {
        setError(apiErrorMessage(err, t("join.invalid"), t));
      }
    } finally {
      setLoading(false);
    }
  }, [joinCode, inviteToken, mode, t]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  const submit = async () => {
    if (!preview || busy) return;
    if (preview.audience === "guest" && !consent) {
      message.warning(t("join.consentRequired"));
      return;
    }
    setBusy(true);
    try {
      const result =
        mode === "invite"
          ? await appsApi.redeemInvite(inviteToken, displayName.trim())
          : await appsApi.redeemJoinCode(joinCode, displayName.trim());
      if (result.guest_token) {
        setGuestSession({
          guestToken: result.guest_token,
          refreshToken: result.refresh_token,
          guestExpiresIn: result.guest_expires_in ?? undefined,
          refreshExpiresIn: result.refresh_expires_in ?? undefined,
          remember,
        });
      }
      if (result.app_token) {
        setAppToken(result.app_id, result.app_token, result.expires_in);
      }
      if (result.status === "pending" || !result.app_token) {
        setPending(true);
        setBusy(false);
        return;
      }
      if (getAuthToken()) {
        navigate(`/apps/${encodeURIComponent(result.app_id)}`, {
          replace: true,
        });
      } else {
        // Enter guest mode: a fresh document re-evaluates the guest session.
        window.location.replace(`/apps/${encodeURIComponent(result.app_id)}`);
      }
    } catch (err) {
      const code = parseApiError(err)?.code;
      if (code === "AUTH_FAILED") {
        setError(t("join.signInRequired"));
      } else if (
        code === "APP_HOST_APPROVAL_REQUIRED" ||
        code === "APP_MEMBER_PENDING"
      ) {
        setHostPending(true);
      } else {
        setError(apiErrorMessage(err, t("join.failed"), t));
      }
      setBusy(false);
    }
  };

  const submitManual = () => {
    const code = manualCode.trim().toUpperCase();
    if (!code) return;
    const next = new URLSearchParams();
    next.set("code", code);
    setSearchParams(next, { replace: true });
  };

  if (!mode) {
    return (
      <Standalone>
        <Title level={3}>{t("join.title")}</Title>
        <Text type="secondary">{t("join.manualHint")}</Text>
        <Space.Compact style={{ width: "100%", marginTop: 16 }}>
          <Input
            value={manualCode}
            onChange={(event) =>
              setManualCode(event.target.value.toUpperCase())
            }
            onPressEnter={submitManual}
            placeholder={t("join.codePlaceholder")}
          />
          <Button type="primary" onClick={submitManual}>
            {t("join.continue")}
          </Button>
        </Space.Compact>
      </Standalone>
    );
  }

  if (loading) {
    return (
      <Standalone>
        <div style={{ textAlign: "center", padding: 48 }}>
          <Spin size="large" />
        </div>
      </Standalone>
    );
  }

  if (hostPending) {
    return (
      <Standalone>
        <Title level={3}>{t("join.hostPendingTitle")}</Title>
        <Text type="secondary">{t("join.hostPendingBody")}</Text>
        <Button
          style={{ marginTop: 16 }}
          onClick={() => window.location.reload()}
        >
          {t("join.retry")}
        </Button>
      </Standalone>
    );
  }

  if (error || !preview) {
    return (
      <Standalone>
        <Title level={3}>{t("join.errorTitle")}</Title>
        <Alert type="error" showIcon message={error || t("join.invalid")} />
        <Button style={{ marginTop: 16 }} onClick={() => navigate("/login")}>
          {t("join.backToLogin")}
        </Button>
      </Standalone>
    );
  }

  if (pending) {
    return (
      <Standalone>
        <Title level={3}>{t("join.pendingTitle")}</Title>
        <Text type="secondary">{t("join.pendingBody")}</Text>
        <Button
          type="primary"
          style={{ marginTop: 16 }}
          onClick={() =>
            pendingGuest ? window.location.replace("/my") : navigate("/apps")
          }
        >
          {pendingGuest ? t("join.goMy") : t("join.goApps")}
        </Button>
      </Standalone>
    );
  }

  return (
    <Standalone>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <div>
          <Title level={3} style={{ marginBottom: 4 }}>
            {t("join.titleWithApp", { name: preview.appName })}
          </Title>
          <Space wrap>
            <Tag color={preview.audience === "guest" ? "purple" : "blue"}>
              {preview.audience === "guest"
                ? t("apps.memberGuest")
                : t("apps.memberMember")}
            </Tag>
            <Tag>{roleText(preview.role, t)}</Tag>
            {preview.requiresApproval && (
              <Tag color="gold">{t("join.needsApproval")}</Tag>
            )}
          </Space>
        </div>
        {preview.audience === "guest" && (
          <Input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder={t("join.displayName")}
            maxLength={100}
          />
        )}
        <Checkbox
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
        >
          {t("join.consent")}
        </Checkbox>
        <Checkbox
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
        >
          {t("join.remember")}
        </Checkbox>
        <Button
          type="primary"
          size="large"
          block
          loading={busy}
          disabled={preview.audience === "guest" && !consent}
          onClick={() => void submit()}
        >
          {t("join.submit")}
        </Button>
      </Space>
    </Standalone>
  );
}

function Standalone({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        background: "var(--fn-bg-layout)",
      }}
    >
      <Card style={{ maxWidth: 520, width: "100%" }}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {children}
        </Space>
      </Card>
    </div>
  );
}
