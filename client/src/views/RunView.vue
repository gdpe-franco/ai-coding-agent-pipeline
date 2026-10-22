<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import EventTimeline from "../components/EventTimeline.vue";
import PreviewFrame from "../components/PreviewFrame.vue";
import StatusBadge from "../components/StatusBadge.vue";
import type { Run, RunEvent } from "../types";

const route = useRoute();
const runId = String(route.params.id);
const run = ref<Run | null>(null);
const events = ref<RunEvent[]>([]);
const error = ref("");
const guidance = ref("");
const activeFile = ref("");
let timer: number | undefined;

const terminal = computed(() => run.value?.status === "completed" || run.value?.status === "failed");
const files = computed(() => run.value?.latest_code ?? []);
const selectedContent = computed(
  () => files.value.find((file) => file.file_path === activeFile.value)?.content ?? "",
);

async function load() {
  try {
    run.value = await request<Run>(`/runs/${runId}`);
    events.value = await request<RunEvent[]>(`/runs/${runId}/events`);
    error.value = "";
    if (run.value.status === "completed" || run.value.status === "failed") {
      window.clearInterval(timer);
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load the run";
  }
}

async function resume() {
  if (!guidance.value.trim()) {
    return;
  }
  try {
    await request(`/runs/${runId}/resume`, {
      method: "POST",
      body: JSON.stringify({ guidance: guidance.value.trim() }),
    });
    guidance.value = "";
    await load();
    startPolling();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not resume the run";
  }
}

async function close() {
  if (!window.confirm("Close this handoff? The run will be marked failed and its files are kept.")) {
    return;
  }
  try {
    await request(`/runs/${runId}/close`, { method: "POST" });
    await load();
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not close the run";
  }
}

function startPolling() {
  window.clearInterval(timer);
  timer = window.setInterval(() => {
    if (!terminal.value) {
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
  <div v-if="error" class="card border-rose-800 text-rose-200">{{ error }}</div>

  <div v-if="run" class="space-y-6">
    <header class="card flex flex-wrap items-start justify-between gap-4">
      <div>
        <div class="flex items-center gap-3">
          <h1 class="text-2xl font-semibold">Run</h1>
          <StatusBadge :status="run.status" />
        </div>
        <p class="mt-2 max-w-3xl whitespace-pre-wrap text-sm text-slate-300">{{ run.message }}</p>
        <p class="mt-2 text-xs text-slate-500">
          Round {{ run.round }} · {{ run.attempts_used }} attempt(s) this round · {{ run.attempts_total }} total
        </p>
      </div>
      <button class="btn-secondary" @click="load">Refresh</button>
    </header>

    <div v-if="run.status === 'awaiting_user'" class="card border-amber-700/60 bg-amber-950/20 space-y-3">
      <h2 class="text-lg font-semibold text-amber-200">The reviewer stopped with an honest handoff</h2>
      <p class="text-sm text-amber-100/80">
        Generated files and unresolved issues are preserved. Add guidance to start a fresh round, or close
        the run. This is not an approval.
      </p>
      <textarea v-model="guidance" class="input" rows="3" placeholder="What should change?" />
      <div class="flex gap-2">
        <button class="btn" :disabled="!guidance.trim()" @click="resume">Resume with guidance</button>
        <button class="btn-secondary" @click="close">Close run</button>
      </div>
    </div>

    <div v-if="run.status === 'completed' && run.project_id" class="card space-y-3">
      <h2 class="text-lg font-semibold">Preview</h2>
      <PreviewFrame :project-id="run.project_id" />
    </div>

    <div v-if="run.chat_reply" class="card text-sm text-slate-200">{{ run.chat_reply }}</div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Plan</h2>
        <p v-if="run.plan" class="text-sm text-slate-300">{{ run.plan.summary }}</p>
        <ol v-if="run.plan" class="space-y-2">
          <li v-for="step in run.plan.steps" :key="step.id" class="rounded-lg border border-slate-800 p-3">
            <p class="font-mono text-sm text-indigo-300">{{ step.file_path }}</p>
            <p class="text-sm text-slate-300">{{ step.description }}</p>
          </li>
        </ol>
        <p v-else class="text-sm text-slate-500">No plan yet.</p>
      </section>

      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Deterministic checks</h2>
        <div v-if="run.checks" class="space-y-1 text-sm">
          <p :class="run.checks.file_structure_ok ? 'text-emerald-300' : 'text-rose-300'">
            File structure: {{ run.checks.file_structure_ok ? "pass" : "fail" }}
          </p>
          <p :class="run.checks.js_syntax_ok ? 'text-emerald-300' : 'text-rose-300'">
            JavaScript syntax: {{ run.checks.js_syntax_ok ? "pass" : "fail" }}
          </p>
          <ul v-if="run.checks.issues.length" class="list-disc pl-5 text-slate-300">
            <li v-for="issue in run.checks.issues" :key="issue">{{ issue }}</li>
          </ul>
        </div>
        <p v-else class="text-sm text-slate-500">Checks run before the reviewer.</p>
      </section>
    </div>

    <div class="grid gap-6 lg:grid-cols-2">
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Files</h2>
        <div class="flex max-h-56 flex-wrap gap-2 overflow-auto">
          <button
            v-for="file in files"
            :key="file.file_path"
            class="rounded border px-2 py-1 font-mono text-xs"
            :class="activeFile === file.file_path ? 'border-indigo-400 text-indigo-200' : 'border-slate-700 text-slate-300'"
            @click="activeFile = file.file_path"
          >
            {{ file.file_path }}
          </button>
          <p v-if="files.length === 0" class="text-sm text-slate-500">No files written yet.</p>
        </div>
        <pre v-if="selectedContent" class="max-h-96 overflow-auto rounded-lg bg-slate-950 p-3 text-xs text-slate-200"><code>{{ selectedContent }}</code></pre>
      </section>

      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Review</h2>
        <div v-if="run.feedback" class="space-y-2 text-sm">
          <p class="font-semibold" :class="run.feedback.verdict === 'approve' ? 'text-emerald-300' : 'text-amber-300'">
            {{ run.feedback.verdict }}
          </p>
          <p class="text-slate-300">{{ run.feedback.summary }}</p>
          <ul class="list-disc space-y-1 pl-5 text-slate-300">
            <li v-for="(issue, index) in run.feedback.issues" :key="index">
              <span v-if="issue.file_path" class="font-mono text-xs text-indigo-300">{{ issue.file_path }}: </span>
              {{ issue.message }}
            </li>
          </ul>
        </div>
        <p v-else class="text-sm text-slate-500">No review yet.</p>
      </section>
    </div>

    <section class="card space-y-3">
      <h2 class="text-lg font-semibold">Event timeline</h2>
      <EventTimeline :events="events" />
    </section>
  </div>
</template>
