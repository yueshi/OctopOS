import { Button, Checkbox } from "antd";
import { useTranslation } from "react-i18next";

import { CAP } from "../../../components/MiniAppFrame/capabilities";
import { InfoSection } from "./shared";

const CAPABILITY_OPTIONS: {
  value: string;
  labelKey: string;
  fallback: string;
}[] = [
  {
    value: CAP.DATA_READ,
    labelKey: "apps.capabilities.dataRead",
    fallback: "Read tables and rows",
  },
  {
    value: CAP.DATA_WRITE,
    labelKey: "apps.capabilities.dataWrite",
    fallback: "Append rows",
  },
  {
    value: CAP.ASSET_READ,
    labelKey: "apps.capabilities.assetRead",
    fallback: "Load app images",
  },
  {
    value: CAP.EXTERNAL_OPEN,
    labelKey: "apps.capabilities.externalOpen",
    fallback: "Open external links",
  },
  {
    value: CAP.CHAT_COMPOSE,
    labelKey: "apps.capabilities.chatCompose",
    fallback: "Prepare a chat message",
  },
  {
    value: CAP.EVENTS_READ,
    labelKey: "apps.capabilities.eventsRead",
    fallback: "Read app events",
  },
  {
    value: CAP.AI_GENERATE,
    labelKey: "apps.capabilities.aiGenerate",
    fallback: "Call the AI model",
  },
  {
    value: CAP.GAME_LAUNCH,
    labelKey: "apps.capabilities.gameLaunch",
    fallback: "Launch external games",
  },
  {
    value: CAP.EXTERNAL_FETCH,
    labelKey: "apps.capabilities.externalFetch",
    fallback: "Call external services",
  },
  {
    value: CAP.EVENTS_EMIT,
    labelKey: "apps.capabilities.eventsEmit",
    fallback: "Send events and alerts",
  },
  {
    value: CAP.SCHEDULES_MANAGE,
    labelKey: "apps.capabilities.schedulesManage",
    fallback: "Manage schedules",
  },
];

interface Props {
  canManage: boolean;
  draft: string[];
  saving: boolean;
  onChange: (values: string[]) => void;
  onSave: () => void;
}

export default function CapabilitiesSection({
  canManage,
  draft,
  saving,
  onChange,
  onSave,
}: Props) {
  const { t } = useTranslation();

  return (
    <InfoSection
      title={t("apps.capabilities.title", "Page capabilities")}
      hint={t(
        "apps.capabilities.hint",
        "Controls what the MiniApp page may ask the host to do; agent tools and this dashboard are not affected.",
      )}
    >
      <Checkbox.Group
        value={draft}
        onChange={(values) => onChange(values as string[])}
        disabled={!canManage || saving}
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))",
          columnGap: 12,
          rowGap: 8,
        }}
      >
        {CAPABILITY_OPTIONS.map((option) => (
          <Checkbox key={option.value} value={option.value}>
            {t(option.labelKey, option.fallback)}
          </Checkbox>
        ))}
      </Checkbox.Group>
      {canManage && (
        <Button
          type="primary"
          size="small"
          style={{ marginTop: 12 }}
          loading={saving}
          onClick={onSave}
        >
          {t("apps.capabilities.save", "Save")}
        </Button>
      )}
    </InfoSection>
  );
}
