import { useTranslation } from "react-i18next";

import CapabilityPicker from "../../../components/MiniAppFrame/CapabilityPicker";
import { InfoSection } from "./shared";

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
      <CapabilityPicker
        value={draft}
        disabled={!canManage}
        saving={saving}
        onChange={onChange}
        onSave={canManage ? onSave : undefined}
      />
    </InfoSection>
  );
}
