import type { TFunction } from "i18next";

import type { AppPermission } from "../../api/modules/apps";

/** Human-readable label for the caller's effective MiniApp permission. */
export function permissionLabel(
  permission: AppPermission,
  t: TFunction,
  isOwner?: boolean,
): string {
  if (permission === "edit_structure") {
    return isOwner === false
      ? t("apps.permissionAdmin", "Administrator")
      : t("apps.editStructure", "Owner");
  }
  if (permission === "edit_data") return t("apps.editData", "Can add rows");
  if (permission === "participate") {
    return t("apps.memberParticipate", "Participant");
  }
  return t("apps.viewOnly", "View only");
}
