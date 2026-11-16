import type { AppAccessPermission } from "../../../api/modules/apps";

export interface ShareDraft {
  username: string;
  permission: AppAccessPermission;
}
