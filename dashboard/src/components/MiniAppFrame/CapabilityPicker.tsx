import { Button, Checkbox } from "antd";
import { useTranslation } from "react-i18next";

import { CAP } from "./capabilities";

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
    value: CAP.AI_TASK,
    labelKey: "apps.capabilities.aiTask",
    fallback: "Run AI tasks",
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
    value: CAP.EXTERNAL_WRITE,
    labelKey: "apps.capabilities.externalWrite",
    fallback: "Submit work to external services",
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

export interface CapabilityPickerProps {
  value: string[];
  disabled?: boolean;
  saving?: boolean;
  onChange: (values: string[]) => void;
  /** Renders the save button when provided. */
  onSave?: () => void;
}

/**
 * Capability checklist shared by the app detail drawer and the chat MiniApp
 * info drawer. The surrounding section title/hint stays with the host.
 */
export default function CapabilityPicker({
  value,
  disabled = false,
  saving = false,
  onChange,
  onSave,
}: CapabilityPickerProps) {
  const { t } = useTranslation();

  return (
    <>
      <Checkbox.Group
        value={value}
        onChange={(values) => onChange(values as string[])}
        disabled={disabled || saving}
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(170px, 1fr))",
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
      {onSave && (
        <Button
          type="primary"
          size="small"
          style={{ marginTop: 12 }}
          loading={saving}
          disabled={disabled}
          onClick={onSave}
        >
          {t("apps.capabilities.save", "Save capabilities")}
        </Button>
      )}
    </>
  );
}
