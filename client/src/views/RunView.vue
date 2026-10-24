<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import EventTimeline from "../components/EventTimeline.vue";
import FileExplorer from "../components/FileExplorer.vue";
import StatusBadge from "../components/StatusBadge.vue";
import type { Project, Run, RunArtifact, RunEvent } from "../types";

const route = useRoute();
const runId = computed(() => String(route.params.id));
const run = ref<Run | null>(null);
const project = ref<Project | null>(null);
const previewUrl = ref("");
const events = ref<RunEvent[]>([]);
const artifacts = ref<RunArtifact[]>([]);
const selectedExecutionId = ref("");
const loading = ref(false);
const error = ref("");
const guidance = ref("");
let pendingContinuation: { guidance: string; key: string } | null = null;
let timer: number | undefined;
let refreshPromise: Promise<void> | undefined;

const processing = computed(() => run.value?.status === "pending" || run.value?.status === "running");
const artifactExecutions = computed(() => {
  const groups = new Map<string, RunArtifact[]>();
  for (const artifact of artifacts.value) {
    groups.set(artifact.execution_id, [...(groups.get(artifact.execution_id) ?? []), artifact]);
  }
  return [...groups.entries()];
});
const files = computed(() => {
  if (selectedExecutionId.value) {
    return (
      artifactExecutions.value.find(([executionId]) => executionId === selectedExecutionId.value)?.[1] ?? []
    ).map((artifact) => artifact.file_path);
  }
  return (run.value?.latest_code ?? []).map((file) => file.file_path);
});

async function load() {
  if (refreshPromise) return refreshPromise;
  const currentRunId = runId.value;
  loading.value = run.value?.id !== currentRunId;
  const pending = (async () => {
    try {
      const [latestRun, latestEvents, latestArtifacts] = await Promise.all([
        request<Run>(`/runs/${currentRunId}`),
        request<RunEvent[]>(`/runs/${currentRunId}/events`),
        request<RunArtifact[]>(`/runs/${currentRunId}/artifacts`),
      ]);
      if (currentRunId !== runId.value) return;
      const refreshProject =
        project.value?.id !== latestRun.project_id ||
        (run.value?.status !== latestRun.status && latestRun.status === "completed");
      run.value = latestRun;
      events.value = latestEvents;
      artifacts.value = latestArtifacts;
      if (refreshProject) {
        project.value = await request<Project>(`/projects/${latestRun.project_id}`);
      }
      if (project.value?.preview_ready && (refreshProject || !previewUrl.value)) {
        previewUrl.value = (
          await request<{ url: string }>(`/projects/${latestRun.project_id}/preview-url`)
        ).url;
      } else if (!project.value?.preview_ready) {
        previewUrl.value = "";
      }
      if (currentRunId !== runId.value) return;
      if (processing.value) selectedExecutionId.value = "";
      else selectedExecutionId.value = artifactExecutions.value.at(-1)?.[0] ?? "";
      error.value = "";
      if (!processing.value) {
        window.clearInterval(timer);
      }
    } catch (cause) {
      if (currentRunId === runId.value) {
        error.value = cause instanceof Error ? cause.message : "Could not load the run";
      }
    } finally {
      if (currentRunId === runId.value) loading.value = false;
    }
  })();
  refreshPromise = pending;
  try {
    await pending;
  } finally {
    if (refreshPromise === pending) refreshPromise = undefined;
  }
}

async function loadFile(path: string): Promise<string> {
  if (!selectedExecutionId.value) {
    return run.value?.latest_code.find((file) => file.file_path === path)?.content ?? "";
  }
  const artifact = artifacts.value.find(
    (item) => item.execution_id === selectedExecutionId.value && item.file_path === path,
  );
  if (!artifact) return "";
  const result = await request<{ content: string }>(`/runs/${runId.value}/artifacts/${artifact.id}`);
  return result.content;
}

async function continueRun() {
  if (!guidance.value.trim()) {
    return;
  }
  try {
    if (pendingContinuation?.guidance !== guidance.value.trim()) {
      pendingContinuation = { guidance: guidance.value.trim(), key: crypto.randomUUID() };
    }
    await request(`/runs/${runId.value}/continuations`, {
      method: "POST",
      headers: { "Idempotency-Key": pendingContinuation.key },
      body: JSON.stringify({ guidance: guidance.value.trim() }),
    });
    pendingContinuation = null;
    guidance.value = "";
    await load();
    startPolling();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not continue the run";
  }
}

watch(runId, async () => {
  window.clearInterval(timer);
  run.value = null;
  project.value = null;
  previewUrl.value = "";
  events.value = [];
  artifacts.value = [];
  selectedExecutionId.value = "";
  guidance.value = "";
  pendingContinuation = null;
  error.value = "";
  if (refreshPromise) await refreshPromise;
  await load();
  startPolling();
});

async function close() {
  if (!window.confirm("Close this handoff? The run will be marked failed and its files are kept.")) {
    return;
  }
  try {
    await request(`/runs/${runId.value}/close`, { method: "POST" });
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not close the run";
  }
}

function startPolling() {
  window.clearInterval(timer);
  if (!processing.value) return;
  timer = window.setInterval(() => {
    if (processing.value) {
      void load();
    }
  }, 2000);
}

onMounted(async () => {
  await load();
  startPolling();
});

onUnmounted(() => window.clearInterval(timer));
</script>

<template>
  <div v-if="error" class="card flex flex-wrap items-center justify-between gap-3 border-danger-line bg-danger-soft text-danger">
    <span>{{ error }}</span>
    <button class="btn-secondary" type="button" @click="load">Retry loading</button>
  </div>
  <p v-if="loading && !run" class="card text-sm text-muted" role="status">Loading run details…</p>

  <div v-if="run" class="min-w-0 space-y-5">
    <section class="card min-w-0 space-y-4 p-4 sm:p-5" aria-labelledby="project-heading">
      <div class="w-full min-w-0">
        <p class="mono-label">Project</p>
        <h1 id="project-heading" class="mt-1 break-words text-2xl font-semibold">
          {{ project?.title ?? "Project" }}
        </h1>
        <p class="mt-2 whitespace-pre-wrap break-words text-sm text-paper-dim">
          {{ project?.summary || "Your project workspace." }}
        </p>
      </div>
      <div class="flex w-full flex-wrap items-center gap-2 border-t border-line pt-4">
        <a
          v-if="previewUrl"
          class="btn w-full sm:w-auto"
          :href="previewUrl"
          target="_blank"
          rel="noopener noreferrer"
        >
          Open latest approved preview ↗
        </a>
        <p v-else class="w-full text-sm text-faint sm:w-auto">No approved preview yet.</p>
        <RouterLink class="btn-secondary w-full sm:w-auto" :to="`/chats/${run.chat_id}`">
          Back to chat
        </RouterLink>
        <RouterLink class="btn-secondary w-full sm:w-auto" :to="`/chats/${run.chat_id}/history`">
          Run history
        </RouterLink>
      </div>
    </section>

    <section class="card min-w-0 space-y-4 p-4 sm:p-5" aria-label="Run details">
      <div class="flex min-w-0 flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <p class="mono-label">Run ID</p>
          <p class="break-all font-mono text-xs text-faint">{{ run.id }}</p>
        </div>
        <StatusBadge :status="run.status" />
      </div>
      <div class="space-y-2">
        <p class="mono-label">Request</p>
        <p class="whitespace-pre-wrap break-words text-sm leading-6 text-paper-dim">{{ run.message }}</p>
        <p class="text-xs text-faint">Started {{ new Date(run.created_at).toLocaleString() }}</p>
      </div>
      <div v-if="run.chat_reply" class="space-y-2 border-t border-line pt-4">
        <p class="mono-label">Result</p>
        <p class="whitespace-pre-wrap break-words text-sm leading-6 text-paper-dim">{{ run.chat_reply }}</p>
      </div>
    </section>

    <div v-if="run.status === 'awaiting_user'" class="card space-y-3 border-warning-line bg-warning-soft">
      <h2 class="text-lg font-semibold text-warning">This request needs your guidance</h2>
      <p class="text-sm text-paper-dim">
        The saved files and open questions are shown below. Add guidance to continue this request, or close
        it. This result is not approved.
      </p>
      <textarea v-model="guidance" class="input" rows="3" placeholder="What should change?" />
      <div class="flex gap-2">
        <button class="btn w-full sm:w-auto" :disabled="!guidance.trim()" @click="continueRun">Continue with guidance</button>
        <button class="btn-secondary w-full sm:w-auto" @click="close">Close run</button>
      </div>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="card min-w-0 space-y-3 p-4 sm:p-5">
        <h2 class="text-lg font-semibold">Plan</h2>
        <p v-if="run.plan" class="text-sm text-paper-dim">{{ run.plan.summary }}</p>
        <ol v-if="run.plan" class="space-y-2">
          <li v-for="step in run.plan.steps" :key="step.id" class="rounded-lg border border-line p-3">
            <p class="font-mono text-sm text-accent">{{ step.file_path }}</p>
            <p class="text-sm text-paper-dim">{{ step.description }}</p>
          </li>
        </ol>
        <p v-else class="text-sm text-faint">No plan yet.</p>
      </section>

      <section class="card min-w-0 space-y-3 p-4 sm:p-5">
        <h2 class="text-lg font-semibold">Deterministic checks</h2>
        <div v-if="run.checks" class="space-y-1 text-sm">
          <p :class="run.checks.file_structure_ok ? 'text-success' : 'text-danger'">
            File structure: {{ run.checks.file_structure_ok ? "pass" : "fail" }}
          </p>
          <p :class="run.checks.js_syntax_ok ? 'text-success' : 'text-danger'">
            JavaScript syntax: {{ run.checks.js_syntax_ok ? "pass" : "fail" }}
          </p>
          <ul v-if="run.checks.issues.length" class="list-disc pl-5 text-paper-dim">
            <li v-for="issue in run.checks.issues" :key="issue">{{ issue }}</li>
          </ul>
        </div>
        <p v-else class="text-sm text-faint">Checks run before the reviewer.</p>
      </section>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="card min-w-0 space-y-3 p-4 sm:p-5">
        <h2 class="text-lg font-semibold">Review</h2>
        <div v-if="run.feedback" class="space-y-2 text-sm">
          <p class="font-semibold" :class="run.feedback.verdict === 'approve' ? 'text-success' : 'text-warning'">
            {{ run.feedback.verdict }}
          </p>
          <p class="text-paper-dim">{{ run.feedback.summary }}</p>
          <ul class="list-disc space-y-1 pl-5 text-paper-dim">
            <li v-for="(issue, index) in run.feedback.issues" :key="index">
              <span v-if="issue.file_path" class="font-mono text-xs text-accent">{{ issue.file_path }}: </span>
              {{ issue.message }}
            </li>
          </ul>
        </div>
        <p v-else class="text-sm text-faint">No review yet.</p>
      </section>
    </div>

    <FileExplorer title="Files" :files="files" :load-file="loadFile">
      <template #actions>
        <label v-if="artifactExecutions.length" class="flex items-center gap-2 text-xs text-muted">
          Saved execution
          <select v-model="selectedExecutionId" class="input w-auto py-1">
            <option value="">Current run output</option>
            <option v-for="([executionId], index) in artifactExecutions" :key="executionId" :value="executionId">
              Execution {{ index + 1 }}
            </option>
          </select>
        </label>
      </template>
    </FileExplorer>

    <section class="card min-w-0 space-y-3 p-4 sm:p-5">
      <h2 class="text-lg font-semibold">Event timeline</h2>
      <EventTimeline :events="events" />
    </section>
  </div>
</template>
