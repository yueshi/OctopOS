import { useEffect, useRef, useState } from "react";
import { Alert, Button, Card, Result, Space, Spin, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { setAppScope, setAuthToken } from "../../api";
import { authApi, type AppAccessPreview } from "../../api/modules/auth";
import { refreshServerLabels } from "../../i18n";
import { applyUserLocale } from "../../utils/locale";
import { apiErrorMessage } from "../../utils/apiError";
import { message } from "@/utils/antdMessage";
import { safeRedirect } from "./OidcComplete";

/** Prefer the URL fragment; the query fallback helps older links. */
export function readAppAccessParams(
  hash: string,
  search: string,
): { code: string | null } {
  const fromHash = new URLSearchParams(
    hash.startsWith("#") ? hash.slice(1) : hash,
  );
  const fromQuery = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  return { code: fromHash.get("code") || fromQuery.get("code") };
}

export default function AppAccessComplete() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const did = useRef(false);
  const [code, setCode] = useState<string | null>(null);
  const [preview, setPreview] = useState<AppAccessPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (did.current) return;
    did.current = true;

    const params = readAppAccessParams(
      window.location.hash,
      window.location.search,
    );
    if (window.location.hash || window.location.search) {
      window.history.replaceState(null, "", window.location.pathname);
    }
    if (!params.code) {
      setError(t("appAccess.missingCode"));
      return;
    }
    setCode(params.code);
    void authApi
      .previewAppAccess(params.code)
      .then(setPreview)
      .catch((err) => {
        setError(apiErrorMessage(err, t("appAccess.invalid"), t));
      });
  }, [t]);

  const confirm = () => {
    if (!code || busy) return;
    setBusy(true);
    void authApi
      .exchangeAppAccess(code)
      .then(async (res) => {
        // The exchanged token is app-scoped and short-lived; keep it in this
        // tab only and tell the request layer not to treat it as a full login.
        setAuthToken(res.access_token, false);
        setAppScope({
          app_id: res.app_id,
          expires_at: Math.floor(Date.now() / 1000) + res.expires_in,
        });
        await applyUserLocale(res.user.locale);
        void refreshServerLabels(res.user.locale);
        navigate(safeRedirect(res.redirect), { replace: true });
      })
      .catch((err) => {
        const text = apiErrorMessage(err, t("appAccess.invalid"), t);
        setBusy(false);
        setError(text);
        message.error(text);
      });
  };

  if (error) {
    return (
      <Result
        status="error"
        title={t("appAccess.errorTitle")}
        subTitle={error}
        extra={
          <Button type="primary" onClick={() => navigate("/login")}>
            {t("appAccess.backToLogin")}
          </Button>
        }
      />
    );
  }

  if (!preview) {
    return (
      <div
        style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}
      >
        <Spin size="large" />
      </div>
    );
  }

  const remainingMinutes = Math.max(
    1,
    Math.round((preview.expires_at * 1000 - Date.now()) / 60_000),
  );

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <Card style={{ maxWidth: 520, width: "100%" }}>
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <div>
            <Typography.Title level={3} style={{ marginBottom: 4 }}>
              {t("appAccess.title", { name: preview.app_name })}
            </Typography.Title>
            <Typography.Text type="secondary">
              {t("appAccess.signInAs", { username: preview.username })}
            </Typography.Text>
          </div>
          <Alert
            type="warning"
            showIcon
            message={t("appAccess.warning")}
            description={t("appAccess.remaining", {
              minutes: remainingMinutes,
            })}
          />
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Button
              type="primary"
              block
              size="large"
              loading={busy}
              onClick={confirm}
            >
              {t("appAccess.confirm")}
            </Button>
            <Button block onClick={() => navigate("/login")}>
              {t("appAccess.backToLogin")}
            </Button>
          </Space>
        </Space>
      </Card>
    </div>
  );
}
