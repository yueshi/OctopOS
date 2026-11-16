import {
  Alert,
  Button,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { Download, Plus, Upload as UploadIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type {
  AppColumn,
  AppDetail,
  AppRowMeta,
} from "../../../api/modules/apps";

const { Text } = Typography;

export const SOURCE_COLUMN_WIDTH = 90;
export const ACTIONS_COLUMN_WIDTH = 150;

/** Fixed width for a data column, by its schema type. */
export function dataColumnWidth(type: string): number {
  switch (type) {
    case "integer":
    case "number":
      return 110;
    case "boolean":
      return 100;
    case "date":
      return 130;
    case "datetime":
      return 170;
    case "enum":
      return 140;
    case "text":
      return 260;
    default:
      return 180;
  }
}

export interface DataRowView {
  row: Record<string, string>;
  meta?: AppRowMeta;
}

interface Props {
  tables: AppDetail["tables"];
  dataTable: string;
  onTableChange: (table: string) => void;
  canEditRows: boolean;
  columns: AppColumn[];
  rows: DataRowView[];
  loading: boolean;
  page: number;
  pageSize: number;
  total: number;
  tableWidth: number;
  search: string;
  onSearchChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onAddRow: () => void;
  onExport: () => void;
  onBackup: () => void;
  onEditRow: (
    meta: AppRowMeta | undefined,
    row: Record<string, string>,
  ) => void;
  onDeleteRow: (meta: AppRowMeta | undefined) => void;
}

export default function AppDataTab({
  tables,
  dataTable,
  onTableChange,
  canEditRows,
  columns,
  rows,
  loading,
  page,
  pageSize,
  total,
  tableWidth,
  search,
  onSearchChange,
  onPageChange,
  onAddRow,
  onExport,
  onBackup,
  onEditRow,
  onDeleteRow,
}: Props) {
  const { t } = useTranslation();

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        minHeight: 0,
      }}
    >
      <Space wrap>
        {tables.length > 1 && (
          <Space size={4}>
            <Text type="secondary">{t("apps.tableLabel")}</Text>
            <Select
              size="small"
              value={dataTable}
              onChange={onTableChange}
              options={tables.map((item) => ({
                value: item.table_key,
                label: item.label || item.table_key,
              }))}
            />
          </Space>
        )}
        {canEditRows && (
          <Button
            type="primary"
            size="small"
            icon={<Plus size={14} />}
            onClick={onAddRow}
          >
            {t("apps.addRow")}
          </Button>
        )}
        <Button size="small" icon={<UploadIcon size={14} />} onClick={onExport}>
          {t("apps.exportCsv")}
        </Button>
        <Button size="small" icon={<Download size={14} />} onClick={onBackup}>
          {t("apps.backup")}
        </Button>
        <Input.Search
          size="small"
          allowClear
          value={search}
          placeholder={t("apps.searchRows")}
          onChange={(event) => onSearchChange(event.target.value)}
          style={{ width: 200 }}
        />
      </Space>
      {columns.some((column) => column.pii) && (
        <Alert
          type="warning"
          showIcon
          message={t("apps.piiNotice")}
          style={{ marginBottom: 8 }}
        />
      )}
      <Table
        size="small"
        rowKey={(_, index) => `${page}-${index ?? 0}`}
        loading={loading}
        dataSource={rows.map((item) => item.row)}
        locale={{ emptyText: t("apps.emptyRows") }}
        scroll={{ x: tableWidth }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: false,
          onChange: onPageChange,
        }}
        columns={[
          ...columns.map((column) => ({
            title: (
              <Space size={4}>
                {column.label || column.name}
                {column.pii ? <Tag color="red">{t("apps.piiTag")}</Tag> : null}
              </Space>
            ),
            dataIndex: column.name,
            key: column.name,
            width: dataColumnWidth(column.type),
            ellipsis: true,
          })),
          {
            title: t("apps.source"),
            key: "__source",
            width: SOURCE_COLUMN_WIDTH,
            render: (_: unknown, __: unknown, index: number) =>
              rows[index]?.meta?.source || "manual",
          },
          ...(canEditRows
            ? [
                {
                  title: t("apps.actions"),
                  key: "__actions",
                  width: ACTIONS_COLUMN_WIDTH,
                  render: (_: unknown, __: unknown, index: number) => (
                    <Space size={4}>
                      <Button
                        size="small"
                        type="link"
                        disabled={!rows[index]?.meta?.row_id}
                        onClick={() =>
                          onEditRow(rows[index]?.meta, rows[index]?.row ?? {})
                        }
                      >
                        {t("apps.editRow")}
                      </Button>
                      <Popconfirm
                        title={t("apps.rowDeleteConfirm")}
                        okText={t("apps.delete")}
                        cancelText={t("common.cancel")}
                        okButtonProps={{ danger: true }}
                        onConfirm={() => onDeleteRow(rows[index]?.meta)}
                      >
                        <Button
                          size="small"
                          type="link"
                          danger
                          disabled={!rows[index]?.meta?.row_id}
                        >
                          {t("apps.deleteRow")}
                        </Button>
                      </Popconfirm>
                    </Space>
                  ),
                },
              ]
            : []),
        ]}
      />
    </div>
  );
}
