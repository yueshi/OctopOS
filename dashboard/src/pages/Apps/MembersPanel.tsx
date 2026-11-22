import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  Copy,
  Plus,
  RefreshCw,
  ShieldCheck,
  Ticket,
  Users,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  appsApi,
  type AppInvite,
  type AppJoinCode,
  type AppMember,
  type AppMemberPermission,
  type AppTable,
  type ExternalSharingPolicy,
} from "../../api/modules/apps";
import { apiErrorMessage } from "../../utils/apiError";
import { copyText } from "../../utils/copyText";
import { message } from "../../utils/antdMessage";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { useServerTimezone } from "../../hooks/useServerTimezone";

const { Text, Title } = Typography;

const ROLE_OPTIONS: { value: AppMemberPermission; labelKey: string }[] = [
  { value: "participate", labelKey: "apps.memberParticipate" },
  { value: "view", labelKey: "apps.permissionView" },
  { value: "edit_data", labelKey: "apps.permissionEditData" },
  { value: "edit_structure", labelKey: "apps.permissionEditStructure" },
];

function absoluteLink(url: string): string {
  if (!url) return "";
  return url.startsWith("/") ? `${window.location.origin}${url}` : url;
}

interface Props {
  appId: string;
  agentId: string;
  tables: AppTable[];
  canManage: boolean;
  /** The hosting agent's owner: can approve, but not add or edit roles. */
  hostView?: boolean;
  onTableUpdated?: (table: AppTable) => void;
}

export default function MembersPanel({
  appId,
  agentId,
  tables,
  canManage,
  hostView = false,
  onTableUpdated,
}: Props) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [members, setMembers] = useState<AppMember[]>([]);
  const [invites, setInvites] = useState<AppInvite[]>([]);
  const [joinCodes, setJoinCodes] = useState<AppJoinCode[]>([]);
  const [policy, setPolicy] = useState<ExternalSharingPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [memberOpen, setMemberOpen] = useState(false);
  const [guestOpen, setGuestOpen] = useState(false);
  const [memberForm] = Form.useForm<{
    username: string;
    permission: AppMemberPermission;
  }>();
  const [guestName, setGuestName] = useState("");
  const [createdInvite, setCreatedInvite] = useState<AppInvite | null>(null);
  const [createdCode, setCreatedCode] = useState<AppJoinCode | null>(null);
  const [inviteAudience, setInviteAudience] = useState<"guest" | "member">(
    "guest",
  );
  const [joinAudience, setJoinAudience] = useState<"guest" | "member">("guest");
  const [joinMaxUses, setJoinMaxUses] = useState(10);
  const [joinApproval, setJoinApproval] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [memberData, inviteData] = await Promise.all([
        appsApi.members(appId),
        appsApi.invites(appId),
      ]);
      setMembers(memberData.members);
      setInvites(inviteData.invites);
      // Join codes are owner-only; the agent host opened this page to resolve
      // approvals, so a forbidden list must not hide the members table.
      if (canManage) {
        try {
          const codeData = await appsApi.joinCodes(appId);
          setJoinCodes(codeData.join_codes);
        } catch {
          setJoinCodes([]);
        }
      } else {
        setJoinCodes([]);
      }
      try {
        setPolicy(await appsApi.externalSharing(agentId));
      } catch {
        setPolicy(null);
      }
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.membersLoadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [agentId, appId, canManage, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      setBusy(true);
      try {
        await action();
        message.success(success);
        await load();
      } catch (err) {
        message.error(apiErrorMessage(err, t("apps.membersActionFailed"), t));
      } finally {
        setBusy(false);
      }
    },
    [load, t],
  );

  const roleLabel = (permission: AppMemberPermission) => {
    const option = ROLE_OPTIONS.find((item) => item.value === permission);
    return option ? t(option.labelKey) : permission;
  };

  const audienceLabel = (audience: "guest" | "member") =>
    audience === "guest" ? t("apps.memberGuest") : t("apps.memberMember");

  const sourceLabel = (joinedVia: string) => {
    if (joinedVia === "share") return t("apps.memberSourceShare");
    if (joinedVia === "invite") return t("apps.memberSourceInvite");
    if (joinedVia === "join_code") return t("apps.memberSourceJoinCode");
    if (joinedVia === "im") return t("apps.memberSourceIm");
    return joinedVia;
  };

  const statusTag = (status: AppMember["status"]) => {
    const color =
      status === "active" ? "green" : status === "pending" ? "gold" : "default";
    const key =
      status === "active"
        ? "apps.memberStatusActive"
        : status === "pending"
        ? "apps.memberStatusPending"
        : "apps.memberStatusRemoved";
    return <Tag color={color}>{t(key)}</Tag>;
  };

  const addMember = async () => {
    const values = await memberForm.validateFields();
    await run(
      () =>
        appsApi.addMember(appId, {
          username: values.username.trim(),
          permission: values.permission,
        }),
      t("apps.memberAdded"),
    );
    setMemberOpen(false);
    memberForm.resetFields();
  };

  const addGuest = async () => {
    const name = guestName.trim();
    if (!name) return;
    await run(() => appsApi.addGuest(appId, name), t("apps.memberAdded"));
    setGuestOpen(false);
    setGuestName("");
  };

  const copy = async (text: string, okMessage: string) => {
    if (await copyText(text)) message.success(okMessage);
  };

  const memberColumns: ColumnsType<AppMember> = [
    {
      title: t("apps.memberName"),
      key: "name",
      render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.display_name || item.username}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {item.username}
          </Text>
        </Space>
      ),
    },
    {
      title: t("apps.memberKind"),
      dataIndex: "kind",
      width: 90,
      render: (kind: AppMember["kind"]) => (
        <Tag color={kind === "guest" ? "purple" : "blue"}>
          {kind === "guest" ? t("apps.memberGuest") : t("apps.memberMember")}
        </Tag>
      ),
    },
    {
      title: t("apps.memberRole"),
      key: "permission",
      width: 170,
      render: (_, item) => (
        <Select
          size="small"
          value={item.permission}
          disabled={busy || !canManage || item.kind === "guest"}
          options={ROLE_OPTIONS.map((option) => ({
            value: option.value,
            label: t(option.labelKey),
          }))}
          onChange={(permission) =>
            void run(
              () => appsApi.updateMember(appId, item.user_id, { permission }),
              t("apps.memberUpdated"),
            )
          }
        />
      ),
    },
    {
      title: t("apps.memberStatus"),
      key: "status",
      width: 100,
      render: (_, item) => statusTag(item.status),
    },
    {
      title: t("apps.memberSource"),
      dataIndex: "joined_via",
      width: 110,
      render: (joinedVia: string) => sourceLabel(joinedVia),
    },
    {
      title: t("apps.actions"),
      key: "actions",
      width: 150,
      render: (_, item) => {
        const rejecting = item.status === "pending";
        return (
          <Space size={4}>
            {item.status !== "active" && (
              <Button
                size="small"
                type="link"
                disabled={busy}
                onClick={() =>
                  void run(
                    () =>
                      appsApi.updateMember(appId, item.user_id, {
                        status: "active",
                      }),
                    t("apps.memberApproved"),
                  )
                }
              >
                {t("apps.memberApprove")}
              </Button>
            )}
            <Popconfirm
              title={
                rejecting
                  ? t("apps.memberRejectConfirm")
                  : t("apps.memberRemoveConfirm")
              }
              onConfirm={() =>
                void run(
                  () =>
                    canManage
                      ? appsApi.removeMember(appId, item.user_id)
                      : // The agent host has no MANAGE_MEMBERS; the host
                        // approval branch accepts a status-only PATCH.
                        appsApi.updateMember(appId, item.user_id, {
                          status: "removed",
                        }),
                  rejecting
                    ? t("apps.memberRejected")
                    : t("apps.memberRemoved"),
                )
              }
            >
              <Button size="small" danger type="link" disabled={busy}>
                {rejecting ? t("apps.memberReject") : t("apps.memberRemove")}
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  const createInvite = async () => {
    setBusy(true);
    try {
      const invite = await appsApi.createInvite(appId, {
        audience: inviteAudience,
        permission: inviteAudience === "guest" ? "participate" : "view",
        ttl_seconds: 86400,
        max_uses: 1,
      });
      setCreatedInvite(invite);
      await load();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.inviteCreateFailed"), t));
    } finally {
      setBusy(false);
    }
  };

  const createJoinCode = async () => {
    setBusy(true);
    try {
      const code = await appsApi.createJoinCode(appId, {
        audience: joinAudience,
        permission: joinAudience === "guest" ? "participate" : "view",
        ttl_seconds: 7 * 86400,
        max_uses: joinMaxUses,
        requires_approval: joinApproval,
      });
      setCreatedCode(code);
      await load();
    } catch (err) {
      message.error(apiErrorMessage(err, t("apps.joinCodeCreateFailed"), t));
    } finally {
      setBusy(false);
    }
  };

  const hostStatusLabel = (status: AppInvite["host_status"]) => {
    const key =
      status === "pending"
        ? "apps.inviteHostPending"
        : status === "approved"
        ? "apps.inviteHostApproved"
        : status === "rejected"
        ? "apps.inviteHostRejected"
        : "apps.inviteHostNotRequired";
    return <Tag>{t(key)}</Tag>;
  };

  const membersTab = (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Button
          type="primary"
          icon={<Plus size={14} />}
          onClick={() => setMemberOpen(true)}
          disabled={!canManage || busy}
        >
          {t("apps.memberAdd")}
        </Button>
        <Button
          icon={<Users size={14} />}
          onClick={() => setGuestOpen(true)}
          disabled={!canManage || busy}
        >
          {t("apps.memberAddGuest")}
        </Button>
        <Button icon={<RefreshCw size={14} />} onClick={() => void load()}>
          {t("common.refresh")}
        </Button>
      </Space>
      {loading ? (
        <Spin />
      ) : members.length === 0 ? (
        <Empty description={t("apps.memberEmpty")} />
      ) : (
        <Table
          rowKey="user_id"
          size="small"
          columns={memberColumns}
          dataSource={members}
          pagination={false}
          scroll={{ x: 760 }}
        />
      )}
    </Space>
  );

  const invitesTab = (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Space wrap>
          <Select
            value={inviteAudience}
            onChange={setInviteAudience}
            options={[
              { value: "guest", label: t("apps.memberGuest") },
              { value: "member", label: t("apps.memberMember") },
            ]}
            style={{ width: 140 }}
          />
          <Button
            type="primary"
            icon={<Plus size={14} />}
            loading={busy}
            onClick={() => void createInvite()}
            disabled={!canManage}
          >
            {t("apps.inviteCreate")}
          </Button>
        </Space>
        {createdInvite?.invite_url && (
          <Alert
            style={{ marginTop: 12 }}
            type="success"
            showIcon
            message={t("apps.inviteCreated")}
            description={
              <Space.Compact style={{ width: "100%" }}>
                <Input
                  readOnly
                  value={absoluteLink(createdInvite.invite_url)}
                />
                <Button
                  icon={<Copy size={14} />}
                  onClick={() =>
                    void copy(
                      absoluteLink(createdInvite.invite_url),
                      t("apps.inviteCopied"),
                    )
                  }
                />
              </Space.Compact>
            }
          />
        )}
      </Card>
      {invites.length === 0 ? (
        <Empty description={t("apps.inviteEmpty")} />
      ) : (
        invites.map((invite) => (
          <Card key={invite.invite_id} size="small">
            <Space wrap>
              <Tag>{audienceLabel(invite.audience)}</Tag>
              <Tag>{roleLabel(invite.permission)}</Tag>
              {hostStatusLabel(invite.host_status)}
              <Text type="secondary">
                {t("apps.inviteUsed", {
                  used: invite.used_count,
                  max: invite.max_uses,
                })}
              </Text>
              <Text type="secondary">
                {formatServerDateTime(invite.expires_at, timeZone)}
              </Text>
              {invite.host_status === "pending" && (
                <>
                  <Button
                    size="small"
                    onClick={() =>
                      void run(
                        () =>
                          appsApi.updateInviteHost(
                            appId,
                            invite.invite_id,
                            "approved",
                          ),
                        t("apps.inviteApproved"),
                      )
                    }
                  >
                    {t("apps.memberApprove")}
                  </Button>
                  <Button
                    size="small"
                    danger
                    onClick={() =>
                      void run(
                        () =>
                          appsApi.updateInviteHost(
                            appId,
                            invite.invite_id,
                            "rejected",
                          ),
                        t("apps.inviteRejected"),
                      )
                    }
                  >
                    {t("apps.memberReject")}
                  </Button>
                </>
              )}
              {canManage && (
                <Popconfirm
                  title={t("apps.inviteRevokeConfirm")}
                  onConfirm={() =>
                    void run(
                      () => appsApi.revokeInvite(appId, invite.invite_id),
                      t("apps.inviteRevoked"),
                    )
                  }
                >
                  <Button size="small" danger type="link">
                    {t("apps.inviteRevoke")}
                  </Button>
                </Popconfirm>
              )}
            </Space>
          </Card>
        ))
      )}
    </Space>
  );

  const joinCodesTab = (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Space wrap>
          <Select
            value={joinAudience}
            onChange={setJoinAudience}
            options={[
              { value: "guest", label: t("apps.memberGuest") },
              { value: "member", label: t("apps.memberMember") },
            ]}
            style={{ width: 140 }}
          />
          <InputNumber
            min={1}
            max={1000}
            value={joinMaxUses}
            onChange={(value) => setJoinMaxUses(Number(value) || 1)}
            addonBefore={t("apps.joinCodeMaxUses")}
          />
          <Switch
            checked={joinApproval}
            onChange={setJoinApproval}
            checkedChildren={t("apps.joinCodeApproval")}
            unCheckedChildren={t("apps.joinCodeNoApproval")}
          />
          <Button
            type="primary"
            icon={<Ticket size={14} />}
            loading={busy}
            onClick={() => void createJoinCode()}
            disabled={!canManage}
          >
            {t("apps.joinCodeCreate")}
          </Button>
        </Space>
        {createdCode?.code && (
          <Alert
            style={{ marginTop: 12 }}
            type="success"
            showIcon
            message={t("apps.joinCodeCreated")}
            description={
              <Space.Compact style={{ width: "100%" }}>
                <Input readOnly value={createdCode.code} />
                <Button
                  icon={<Copy size={14} />}
                  onClick={() =>
                    void copy(createdCode.code ?? "", t("apps.joinCodeCopied"))
                  }
                />
              </Space.Compact>
            }
          />
        )}
      </Card>
      {joinCodes.length === 0 ? (
        <Empty description={t("apps.joinCodeEmpty")} />
      ) : (
        joinCodes.map((code) => (
          <Card key={code.join_code_id} size="small">
            <Space wrap>
              <Tag>{audienceLabel(code.audience)}</Tag>
              <Tag>{roleLabel(code.permission)}</Tag>
              <Text type="secondary">
                {t("apps.inviteUsed", {
                  used: code.used_count,
                  max: code.max_uses,
                })}
              </Text>
              {code.requires_approval && (
                <Tag color="gold">{t("apps.joinCodeApproval")}</Tag>
              )}
              <Text type="secondary">
                {formatServerDateTime(code.expires_at, timeZone)}
              </Text>
              <Popconfirm
                title={t("apps.joinCodeRevokeConfirm")}
                onConfirm={() =>
                  void run(
                    () => appsApi.revokeJoinCode(appId, code.join_code_id),
                    t("apps.joinCodeRevoked"),
                  )
                }
              >
                <Button size="small" danger type="link">
                  {t("apps.inviteRevoke")}
                </Button>
              </Popconfirm>
            </Space>
          </Card>
        ))
      )}
    </Space>
  );

  const policyTab = (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Space>
            <ShieldCheck size={16} />
            <Title level={5} style={{ margin: 0 }}>
              {t("apps.hostPolicyTitle")}
            </Title>
            {policy && !policy.can_manage && (
              <Tag>{t("apps.hostPolicyReadOnly")}</Tag>
            )}
          </Space>
          <Text type="secondary">{t("apps.hostPolicyHint")}</Text>
          <Space wrap>
            <Switch
              checked={policy?.allow_external_participants ?? false}
              disabled={!policy?.can_manage || busy}
              onChange={(checked) => {
                if (!policy) return;
                setPolicy({ ...policy, allow_external_participants: checked });
              }}
              checkedChildren={t("apps.hostPolicyAllow")}
              unCheckedChildren={t("apps.hostPolicyDeny")}
            />
            <Switch
              checked={policy?.require_host_approval ?? true}
              disabled={!policy?.can_manage || busy}
              onChange={(checked) => {
                if (!policy) return;
                setPolicy({ ...policy, require_host_approval: checked });
              }}
              checkedChildren={t("apps.hostPolicyRequireApproval")}
              unCheckedChildren={t("apps.hostPolicyNoApproval")}
            />
            <InputNumber
              min={0}
              max={1000}
              value={policy?.max_guests_per_app ?? 50}
              disabled={!policy?.can_manage || busy}
              addonBefore={t("apps.hostPolicyMaxGuests")}
              onChange={(value) => {
                if (!policy) return;
                setPolicy({
                  ...policy,
                  max_guests_per_app: Number(value) || 0,
                });
              }}
            />
            {policy?.can_manage && (
              <Button
                type="primary"
                loading={busy}
                onClick={() =>
                  void run(
                    () =>
                      appsApi.updateExternalSharing(agentId, {
                        allow_external_participants:
                          policy.allow_external_participants,
                        max_guests_per_app: policy.max_guests_per_app,
                        require_host_approval: policy.require_host_approval,
                      }),
                    t("apps.hostPolicySaved"),
                  )
                }
              >
                {t("common.save")}
              </Button>
            )}
          </Space>
        </Space>
      </Card>
      <Divider style={{ margin: "4px 0" }} />
      <Title level={5} style={{ margin: 0 }}>
        {t("apps.memberVisibleTables")}
      </Title>
      <Text type="secondary">{t("apps.memberVisibleTablesHint")}</Text>
      {tables.length === 0 ? (
        <Empty />
      ) : (
        tables.map((table) => (
          <Space key={table.table_key} wrap>
            <Text strong>{table.label || table.table_key}</Text>
            <Text type="secondary">{table.table_key}</Text>
            <Switch
              checked={table.member_visible === true}
              disabled={!canManage || busy}
              onChange={(checked) =>
                void run(async () => {
                  const updated = await appsApi.setTableMemberVisible(
                    appId,
                    table.table_key,
                    table.public_visible,
                    checked,
                  );
                  onTableUpdated?.(updated);
                }, t("apps.memberVisibleSaved"))
              }
            />
          </Space>
        ))
      )}
    </Space>
  );

  return (
    <div style={{ paddingBottom: 24 }}>
      {hostView && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("apps.membersReadOnly")}
        />
      )}
      <Tabs
        items={[
          {
            key: "members",
            label: t("apps.membersTab"),
            children: membersTab,
          },
          {
            key: "invites",
            label: t("apps.invitesTab"),
            children: invitesTab,
          },
          ...(canManage
            ? [
                {
                  key: "join-codes",
                  label: t("apps.joinCodesTab"),
                  children: joinCodesTab,
                },
              ]
            : []),
          {
            key: "access",
            label: t("apps.accessSettingsTab"),
            children: policyTab,
          },
        ]}
      />

      <Modal
        title={t("apps.memberAddTitle")}
        open={memberOpen}
        onCancel={() => setMemberOpen(false)}
        onOk={() => void addMember()}
        confirmLoading={busy}
        destroyOnHidden
      >
        <Form
          form={memberForm}
          layout="vertical"
          initialValues={{ permission: "view" }}
        >
          <Form.Item
            name="username"
            label={t("apps.memberUsername")}
            rules={[{ required: true }]}
          >
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item name="permission" label={t("apps.memberRole")}>
            <Select
              options={ROLE_OPTIONS.map((option) => ({
                value: option.value,
                label: t(option.labelKey),
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("apps.memberAddGuestTitle")}
        open={guestOpen}
        onCancel={() => setGuestOpen(false)}
        onOk={() => void addGuest()}
        confirmLoading={busy}
        destroyOnHidden
      >
        <Input
          value={guestName}
          onChange={(event) => setGuestName(event.target.value)}
          placeholder={t("apps.memberGuestName")}
          maxLength={100}
        />
      </Modal>
    </div>
  );
}
