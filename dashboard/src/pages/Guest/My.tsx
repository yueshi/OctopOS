import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Modal,
  Popconfirm,
  Result,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { LogOut, RefreshCw, Trash2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { appsApi, type GuestApp } from "../../api/modules/apps";
import {
  clearAllAppTokens,
  clearGuestSession,
  getGuestRefreshToken,
  hasGuestSession,
  isGuestSessionRemembered,
  setAppToken,
  setGuestSession,
} from "../../api/request";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import { message } from "../../utils/antdMessage";

const { Text, Title } = Typography;

export default function GuestMyPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { modal } = App.useApp();
  const [apps, setApps] = useState<GuestApp[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [deleted, setDeleted] = useState(false);
  const [linkCode, setLinkCode] = useState<{
    appName: string;
    code: string;
  } | null>(null);

  const refreshSession = useCallback(async (): Promise<boolean> => {
    const refresh = getGuestRefreshToken();
    if (!refresh) return false;
    try {
      const pair = await appsApi.refreshGuestSession(refresh);
      setGuestSession({
        guestToken: pair.guest_token,
        refreshToken: pair.refresh_token,
        guestExpiresIn: pair.guest_expires_in,
        refreshExpiresIn: pair.refresh_expires_in,
        remember: isGuestSessionRemembered(),
      });
      return true;
    } catch {
      return false;
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await appsApi.guestApps();
      setApps(data.apps);
    } catch (err) {
      const code = parseApiError(err)?.code;
      // A locally expired guest token is dropped before the request, so the
      // API answers AUTH_FAILED with no bearer; refreshing recovers both cases.
      const sessionProblem =
        code === "APP_SESSION_INVALID" ||
        code === "AUTH_FAILED" ||
        code === "TOKEN_EXPIRED";
      if (sessionProblem && (await refreshSession())) {
        try {
          const data = await appsApi.guestApps();
          setApps(data.apps);
          return;
        } catch (retryErr) {
          setError(apiErrorMessage(retryErr, t("guest.loadFailed"), t));
          return;
        }
      }
      setError(apiErrorMessage(err, t("guest.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [refreshSession, t]);

  useEffect(() => {
    if (!hasGuestSession()) {
      setLoading(false);
      return;
    }
    void load();
  }, [load]);

  const openApp = async (app: GuestApp) => {
    setBusyId(app.app_id);
    try {
      const session = await appsApi.guestAppSession(app.app_id);
      setAppToken(app.app_id, session.access_token, session.expires_in);
      navigate(`/apps/${encodeURIComponent(app.app_id)}`);
    } catch (err) {
      message.error(apiErrorMessage(err, t("guest.openFailed"), t));
    } finally {
      setBusyId("");
    }
  };

  const linkImAccount = async (app: GuestApp) => {
    setBusyId(app.app_id);
    try {
      // `/link-code` is an app route: make sure this tab holds an app token
      // even after a reload dropped sessionStorage.
      const session = await appsApi.guestAppSession(app.app_id);
      setAppToken(app.app_id, session.access_token, session.expires_in);
      const data = await appsApi.createLinkCode(app.app_id);
      setLinkCode({ appName: app.name, code: data.code });
    } catch (err) {
      message.error(apiErrorMessage(err, t("guest.linkCodeFailed"), t));
    } finally {
      setBusyId("");
    }
  };

  const leaveApp = async (app: GuestApp) => {
    setBusyId(app.app_id);
    try {
      await appsApi.leaveGuestApp(app.app_id);
      setApps((prev) => prev.filter((item) => item.app_id !== app.app_id));
      message.success(t("guest.left"));
    } catch (err) {
      message.error(apiErrorMessage(err, t("guest.actionFailed"), t));
    } finally {
      setBusyId("");
    }
  };

  const logout = async () => {
    try {
      await appsApi.revokeGuestSession();
    } catch {
      // The session may already be gone; clearing locally is enough.
    }
    clearGuestSession();
    clearAllAppTokens();
    window.location.replace("/join");
  };

  const deleteAccount = () => {
    modal.confirm({
      title: t("guest.deleteTitle"),
      content: t("guest.deleteConfirm"),
      okButtonProps: { danger: true },
      okText: t("guest.deleteAccount"),
      onOk: async () => {
        try {
          await appsApi.deleteGuestAccount();
          clearGuestSession();
          clearAllAppTokens();
          setDeleted(true);
        } catch (err) {
          message.error(apiErrorMessage(err, t("guest.actionFailed"), t));
        }
      },
    });
  };

  if (deleted) {
    return (
      <Standalone>
        <Result
          status="success"
          title={t("guest.deletedTitle")}
          subTitle={t("guest.deletedBody")}
          extra={
            <Button type="primary" onClick={() => navigate("/join")}>
              {t("guest.useCode")}
            </Button>
          }
        />
      </Standalone>
    );
  }

  if (!hasGuestSession()) {
    return (
      <Standalone>
        <Result
          status="info"
          title={t("guest.noSessionTitle")}
          subTitle={t("guest.noSessionBody")}
          extra={
            <Button type="primary" onClick={() => navigate("/join")}>
              {t("guest.useCode")}
            </Button>
          }
        />
      </Standalone>
    );
  }

  return (
    <Standalone>
      <Space direction="vertical" size="large" style={{ width: "100%" }}>
        <div>
          <Title level={2} style={{ marginBottom: 4 }}>
            {t("guest.title")}
          </Title>
          <Text type="secondary">{t("guest.subtitle")}</Text>
        </div>
        {loading ? (
          <div style={{ textAlign: "center", padding: 32 }}>
            <Spin />
          </div>
        ) : error ? (
          <Result
            status="warning"
            title={t("guest.loadFailedTitle")}
            subTitle={error}
            extra={
              <Button
                icon={<RefreshCw size={14} />}
                onClick={() => void load()}
              >
                {t("common.refresh")}
              </Button>
            }
          />
        ) : apps.length === 0 ? (
          <Empty description={t("guest.empty")}>
            <Button type="primary" onClick={() => navigate("/join")}>
              {t("guest.useCode")}
            </Button>
          </Empty>
        ) : (
          apps.map((app) => (
            <Card key={app.app_id} size="small">
              <Space
                wrap
                style={{ width: "100%", justifyContent: "space-between" }}
              >
                <Space direction="vertical" size={2}>
                  <Text strong>{app.name}</Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {app.app_id}
                  </Text>
                </Space>
                <Space wrap>
                  <Tag>{t("apps.memberParticipate")}</Tag>
                  <Button
                    type="primary"
                    loading={busyId === app.app_id}
                    onClick={() => void openApp(app)}
                  >
                    {t("guest.open")}
                  </Button>
                  <Button
                    loading={busyId === app.app_id}
                    onClick={() => void linkImAccount(app)}
                  >
                    {t("guest.linkCode")}
                  </Button>
                  <Popconfirm
                    title={t("guest.leaveConfirm")}
                    onConfirm={() => void leaveApp(app)}
                  >
                    <Button loading={busyId === app.app_id}>
                      {t("guest.leave")}
                    </Button>
                  </Popconfirm>
                </Space>
              </Space>
            </Card>
          ))
        )}
        <Space wrap>
          <Button icon={<LogOut size={14} />} onClick={() => void logout()}>
            {t("guest.logout")}
          </Button>
          <Button danger icon={<Trash2 size={14} />} onClick={deleteAccount}>
            {t("guest.deleteAccount")}
          </Button>
        </Space>
      </Space>
      <Modal
        open={linkCode != null}
        title={t("guest.linkCodeTitle", { name: linkCode?.appName ?? "" })}
        onCancel={() => setLinkCode(null)}
        onOk={() => setLinkCode(null)}
        okText={t("guest.linkCodeDone")}
        cancelButtonProps={{ style: { display: "none" } }}
      >
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          <Text>{t("guest.linkCodeHint")}</Text>
          <Text strong copyable style={{ fontSize: 24, letterSpacing: 4 }}>
            /link {linkCode?.code ?? ""}
          </Text>
        </Space>
      </Modal>
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
      <Card style={{ maxWidth: 720, width: "100%" }}>{children}</Card>
    </div>
  );
}
