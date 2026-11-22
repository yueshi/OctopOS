import { createGlobalStyle } from "antd-style";
import { ConfigProvider, theme as antdTheme } from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import { lazy, Suspense, useEffect, useState } from "react";
import DesktopWindowControls from "./components/DesktopWindowControls";
import {
  DesktopChromeProvider,
  useDesktopChrome,
} from "./hooks/useDesktopChrome";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useParams,
} from "react-router-dom";
import { useTranslation } from "react-i18next";
import MainLayout from "./layouts/MainLayout";
import LoginPage from "./pages/Login";
import AppAccessComplete from "./pages/Login/AppAccessComplete";
import OidcComplete from "./pages/Login/OidcComplete";
import SetupPage from "./pages/Setup";
import InvitePage from "./pages/Invite";
import JoinPage from "./pages/Join";
import GuestMyPage from "./pages/Guest/My";
import AuthGuard from "./components/AuthGuard";
import OctopSpinner from "./components/OctopSpinner";
import { AntdAppProvider } from "./components/AntdAppProvider";
import GlobalErrorBoundary from "./components/ErrorBoundary";
import { ThemeProvider, useTheme } from "./context/ThemeContext";
import { AgentProvider } from "./context/AgentContext";
import { LayoutModeProvider } from "./context/LayoutModeContext";
import { VoiceOutputProvider } from "./context/VoiceOutputContext";
import { useIsMobile } from "./hooks/useIsMobile";
import { useUnauthorizedRedirect } from "./hooks/useUnauthorizedRedirect";
import { installDesktopExternalLinks } from "./utils/desktopExternalLinks";
import { appsApi } from "./api/modules/apps";
import {
  getAppScope,
  getAppToken,
  getAuthToken,
  hasGuestSession,
  setAppToken,
} from "./api";
import { brandTokensFor } from "./styles/themePalettes";
import "./styles/theme-vars.css";
import "./styles/layout.css";
import "./styles/form-override.css";
import "./styles/spin-override.css";

const AppRuntimePage = lazy(() => import("./pages/Apps/Detail"));

/**
 * Guest runtime: app routes need an app-scoped token; the guest portal token
 * is only valid on `/guest/*`. Mint one for this app before mounting the page.
 */
function GuestAppRoute() {
  const { appId = "" } = useParams();
  const [ready, setReady] = useState(Boolean(appId && getAppToken(appId)));
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (ready) return;
    let alive = true;
    void appsApi
      .guestAppSession(appId)
      .then((session) => {
        if (!alive) return;
        setAppToken(appId, session.access_token, session.expires_in);
        setReady(true);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [appId, ready]);

  if (failed) return <Navigate replace to="/my" />;
  if (!ready) {
    return (
      <div
        style={{
          position: "fixed",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <OctopSpinner />
      </div>
    );
  }
  return (
    <Suspense fallback={<OctopSpinner />}>
      <AppRuntimePage forceRuntime />
    </Suspense>
  );
}

const GlobalStyle = createGlobalStyle`
* {
  margin: 0;
  box-sizing: border-box;
}
`;

function ThemedApp() {
  const { isDark, palette, customColor } = useTheme();
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const desktopChrome = useDesktopChrome();
  // Subscribing to the location makes the tree switch as soon as the one-time
  // link exchanges its code and navigates into the app.
  const location = useLocation();
  const scopedAppId = getAppScope()?.app_id ?? "";
  // A one-time app-access link owns the tab even when a guest session from an
  // earlier visit is still stored; it must reach /login/app-access.
  const guestMode = !getAuthToken() && !scopedAppId && hasGuestSession();
  const brandTokens = brandTokensFor(palette, isDark, customColor);
  // Make antd built-ins (Popconfirm OK/Cancel, Modal default footer, Empty,
  // Pagination, DatePicker, Table… ) follow the current UI language.
  // DatePicker month/weekday labels come from dayjs — keep it in sync too.
  const isZh = i18n.language?.toLowerCase().startsWith("zh") ?? false;
  const antdLocale = isZh ? zhCN : enUS;
  dayjs.locale(isZh ? "zh-cn" : "en");

  useUnauthorizedRedirect();

  useEffect(() => installDesktopExternalLinks(), []);

  // Set document title based on current language
  useEffect(() => {
    document.title = t("app.pageTitle");
  }, [t]);

  const themeConfig = {
    algorithm: isDark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
    token: {
      borderRadius: 10,
      ...(isMobile
        ? {
            fontSize: 15,
            fontSizeSM: 13,
            fontSizeLG: 17,
            fontSizeXL: 22,
            controlHeight: 36,
          }
        : {}),
      ...(isDark
        ? {
            colorBgBase: "#0f1117",
            colorTextBase: "#e7e7ed",
            colorBgContainer: "#0f1117",
            colorBgElevated: "#1a1c28",
            colorBgLayout: "#0b0d14",
            colorBgSpotlight: "rgba(0, 0, 0, 0.85)",
            colorBgMask: "rgba(5, 5, 8, 0.80)",
            colorBorder: "rgba(255,255,255,0.08)",
            colorBorderSecondary: "rgba(255,255,255,0.05)",
            colorText: "rgba(255, 255, 255, 0.92)",
            colorTextSecondary: "rgba(255, 255, 255, 0.64)",
            colorTextTertiary: "rgba(255, 255, 255, 0.38)",
            colorTextQuaternary: "rgba(255, 255, 255, 0.22)",
            colorFill: "rgba(255, 255, 255, 0.06)",
            colorFillSecondary: "rgba(255, 255, 255, 0.04)",
            colorFillTertiary: "rgba(255, 255, 255, 0.03)",
            colorFillQuaternary: "rgba(255, 255, 255, 0.02)",
            colorBgTextHover: "rgba(255, 255, 255, 0.06)",
            ...brandTokens,
            boxShadow: "0px 4px 6px 0px rgba(0, 0, 0, 0.3)",
            boxShadowSecondary:
              "0px 12px 24px -16px rgba(0, 0, 0, 0.2), 0px 8px 40px 0px rgba(0, 0, 0, 0.3)",
          }
        : {
            ...brandTokens,
          }),
    },
    components: isDark
      ? {
          Modal: { headerBg: "#1a1c28", contentBg: "#1a1c28" },
          Input: { colorBgBase: "#0f1117" },
          InputNumber: { colorBgBase: "#0f1117" },
          Select: { colorBgBase: "#0f1117", selectorBg: "#0f1117" },
          DatePicker: { colorBgBase: "#0f1117" },
          Segmented: { itemSelectedBg: "#1a1c28" },
          Card: { colorBgContainer: "#161822" },
          Tooltip: { colorBgSpotlight: "#424242" },
        }
      : {},
  };

  return (
    <ConfigProvider
      theme={themeConfig}
      prefixCls="octop"
      locale={antdLocale}
      spin={{ indicator: <OctopSpinner /> }}
    >
      <AntdAppProvider>
        <DesktopChromeProvider value={desktopChrome}>
          {desktopChrome ? (
            <DesktopWindowControls chrome={desktopChrome} />
          ) : null}
          {guestMode ? (
            // Guest tokens must not mount the dashboard shell: every global
            // request would be refused by the guest-scope allowlist.
            <Routes key={location.pathname}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/login/oidc/complete" element={<OidcComplete />} />
              <Route path="/login/app-access" element={<AppAccessComplete />} />
              <Route path="/join" element={<JoinPage />} />
              <Route path="/invite" element={<InvitePage />} />
              <Route path="/my" element={<GuestMyPage />} />
              <Route path="/apps/:appId" element={<GuestAppRoute />} />
              <Route path="*" element={<Navigate replace to="/my" />} />
            </Routes>
          ) : scopedAppId ? (
            // A one-time personal link holds an app-scoped token. Keep the
            // dashboard shell (AuthGuard/AgentProvider/MainLayout) unmounted:
            // it would only fire global requests that the scoped token blocks.
            <Routes key={location.pathname}>
              <Route path="/login/app-access" element={<AppAccessComplete />} />
              <Route
                path="/apps/:appId"
                element={
                  <Suspense
                    fallback={
                      <div
                        style={{
                          position: "fixed",
                          inset: 0,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <OctopSpinner />
                      </div>
                    }
                  >
                    <AppRuntimePage forceRuntime />
                  </Suspense>
                }
              />
              <Route
                path="*"
                element={<Navigate replace to={`/apps/${scopedAppId}`} />}
              />
            </Routes>
          ) : (
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/login/oidc/complete" element={<OidcComplete />} />
              <Route path="/login/app-access" element={<AppAccessComplete />} />
              <Route path="/join" element={<JoinPage />} />
              <Route path="/setup" element={<SetupPage />} />
              <Route path="/invite" element={<InvitePage />} />
              <Route
                path="/*"
                element={
                  <AuthGuard>
                    <AgentProvider>
                      <LayoutModeProvider>
                        <VoiceOutputProvider>
                          <MainLayout />
                        </VoiceOutputProvider>
                      </LayoutModeProvider>
                    </AgentProvider>
                  </AuthGuard>
                }
              />
            </Routes>
          )}
        </DesktopChromeProvider>
      </AntdAppProvider>
    </ConfigProvider>
  );
}

function App() {
  return (
    // `useTransitions` off: with router transitions on, React keeps the old
    // page mounted while a lazy route chunk downloads and never renders the
    // Suspense fallback, so a nav click looks like it did nothing.
    <BrowserRouter useTransitions={false}>
      <GlobalErrorBoundary>
        <GlobalStyle />
        <ThemeProvider>
          <ThemedApp />
        </ThemeProvider>
      </GlobalErrorBoundary>
    </BrowserRouter>
  );
}

export default App;
