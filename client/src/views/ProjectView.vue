<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ApiError, request } from "../api";
import PreviewFrame from "../components/PreviewFrame.vue";
import { useProjectsStore } from "../stores/projects";
import type { ConversationTurn } from "../types";

const route = useRoute();
const router = useRouter();
const projects = useProjectsStore();
const turns = ref<ConversationTurn[]>([]);
const message = ref("");
const error = ref("");
const submitting = ref(false);

const projectId = String(route.params.id);

onMounted(async () => {
  try {
    await projects.fetch(projectId);
    turns.value = await request<ConversationTurn[]>(`/projects/${projectId}/conversation`);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load the project";
  }
});

async function startRun() {
  error.value = "";
  submitting.value = true;
  try {
    const run = await request<{ run_id: string }>("/runs", {
      method: "POST",
      body: JSON.stringify({ message: message.value.trim(), project_id: projectId }),
    });
    await router.push(`/runs/${run.run_id}`);
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) {
      error.value = "This project already has an active run. Resume or close it before starting another.";
    } else {
      error.value = cause instanceof Error ? cause.message : "Could not start the run";
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="grid gap-6 lg:grid-cols-[2fr_1fr]">
    <div class="space-y-5">
      <div class="card">
        <h1 class="text-2xl font-semibold">{{ projects.current?.title ?? "Project" }}</h1>
        <p class="mt-1 text-sm text-slate-400">{{ projects.current?.summary || "No summary yet." }}</p>
      </div>

      <form class="card space-y-3" @submit.prevent="startRun">
        <h2 class="text-lg font-semibold">Start a run</h2>
        <textarea
          v-model="message"
          class="input min-h-24"
          maxlength="8000"
          placeholder="Add a feature, fix an issue, or change the design…"
          required
        />
        <p v-if="error" class="text-sm text-rose-300">{{ error }}</p>
        <button class="btn" type="submit" :disabled="submitting || !message.trim()">
          {{ submitting ? "Starting…" : "Start run" }}
        </button>
      </form>
    </div>

    <aside class="space-y-5">
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Preview</h2>
        <PreviewFrame v-if="projects.current" :project-id="projects.current.id" />
      </section>

      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Conversation</h2>
        <p v-if="turns.length === 0" class="text-sm text-slate-500">No messages yet.</p>
        <div v-for="turn in turns" :key="`${turn.sequence}-${turn.created_at}`" class="space-y-1">
          <p class="text-xs font-semibold uppercase tracking-wide text-slate-500">{{ turn.role }}</p>
          <p class="whitespace-pre-wrap text-sm text-slate-300">{{ turn.content }}</p>
        </div>
      </section>
    </aside>
  </div>
</template>
