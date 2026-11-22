import type { ReactNode } from "react";
import { Typography } from "antd";

import type { AppDetail } from "../../../api/modules/apps";

const { Text } = Typography;

export function permissionLabel(
  permission: AppDetail["permission"],
  t: (key: string) => string,
  isOwner?: boolean,
): string {
  if (permission === "edit_structure") {
    return isOwner === false
      ? t("apps.permissionAdmin")
      : t("apps.editStructure");
  }
  if (permission === "edit_data") return t("apps.editData");
  if (permission === "participate") return t("apps.memberParticipate");
  return t("apps.viewOnly");
}

/** One titled block in the details drawer; keeps section rhythm consistent. */
export function InfoSection({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section style={{ marginTop: 24 }}>
      <Text strong>{title}</Text>
      {hint ? (
        <div style={{ marginTop: 4 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {hint}
          </Text>
        </div>
      ) : null}
      <div style={{ marginTop: 10 }}>{children}</div>
    </section>
  );
}
