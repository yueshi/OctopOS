import { useState } from "react";
import { Button, Empty, Form, Modal, Spin } from "antd";
import { Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { CronJobSpecOutput } from "../../../api/types";
import {
  CronJobCard,
  ExecuteNowModal,
  JobDrawer,
} from "../../Control/CronJobs/components";
import {
  useCronJobs,
  type CronJobFormValues,
} from "../../Control/CronJobs/useCronJobs";
import styles from "./DesktopManagerDrawer.module.less";

type CronJob = CronJobSpecOutput;

/** Scheduled-task management inside the desktop manager (no console page). */
export default function DesktopTasksPanel() {
  const { t } = useTranslation();
  const [modal, modalContextHolder] = Modal.useModal();
  const {
    jobs,
    loading,
    cronTimezone,
    activeAgentId,
    createJob,
    updateJob,
    deleteJob,
    toggleEnabled,
    executeNow,
    jobToFormValues,
    refetchJobs,
  } = useCronJobs();
  const [form] = Form.useForm<CronJobFormValues>();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editingJob, setEditingJob] = useState<CronJobFormValues | null>(null);
  const [executingJob, setExecutingJob] = useState<CronJob | null>(null);
  const [executing, setExecuting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const openCreate = () => {
    setEditingJob(null);
    form.resetFields();
    setDrawerOpen(true);
  };

  const openEdit = (job: CronJob) => {
    setEditingJob(jobToFormValues(job, cronTimezone));
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setEditingJob(null);
  };

  const removeJob = (jobId: string) => {
    modal.confirm({
      title: t("cronJobs.confirmDelete"),
      content: t("cronJobs.deleteConfirm"),
      okText: t("common.delete"),
      okType: "primary",
      cancelText: t("common.cancel"),
      onOk: async () => {
        await deleteJob(jobId);
      },
    });
  };

  const submit = async (values: CronJobFormValues) => {
    const saved = editingJob?.id
      ? await updateJob(editingJob.id, values)
      : await createJob(values);
    if (saved) closeDrawer();
  };

  const confirmExecute = async () => {
    if (!executingJob) return;
    setExecuting(true);
    try {
      await executeNow(executingJob.id);
      setExecutingJob(null);
    } finally {
      setExecuting(false);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    try {
      await refetchJobs(true);
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div>
      {modalContextHolder}
      <div className={styles.panelToolbar}>
        <div className={styles.panelToolbarMeta}>
          {t("apps.system.manager.tasksSummary", {
            count: jobs.length,
            defaultValue: "{{count}} scheduled tasks",
          })}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button
            size="small"
            icon={<RefreshCw size={14} />}
            loading={refreshing}
            onClick={() => void refresh()}
          >
            {t("common.refresh", "Refresh")}
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<Plus size={14} />}
            onClick={openCreate}
          >
            {t("cronJobs.createJob", "Create Job")}
          </Button>
        </div>
      </div>

      {loading && jobs.length === 0 ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : jobs.length === 0 ? (
        <Empty
          className={styles.empty}
          description={t("cronJobs.noJobs", "No scheduled tasks yet.")}
        >
          <Button type="primary" onClick={openCreate}>
            {t("cronJobs.createJob", "Create Job")}
          </Button>
        </Empty>
      ) : (
        <div className={styles.tasksList}>
          {jobs.map((job) => (
            <CronJobCard
              key={job.id}
              job={job}
              timeZone={cronTimezone}
              onToggleEnabled={(item) => void toggleEnabled(item)}
              onExecuteNow={setExecutingJob}
              onEdit={openEdit}
              onDelete={removeJob}
            />
          ))}
        </div>
      )}

      <JobDrawer
        open={drawerOpen}
        editingJob={editingJob}
        activeAgentId={activeAgentId}
        cronTimezone={cronTimezone}
        form={form}
        onClose={closeDrawer}
        onSubmit={(values) => void submit(values)}
      />
      <ExecuteNowModal
        open={executingJob !== null}
        job={executingJob}
        loading={executing}
        onCancel={() => {
          if (!executing) setExecutingJob(null);
        }}
        onConfirm={() => void confirmExecute()}
      />
    </div>
  );
}
