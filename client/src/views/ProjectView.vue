<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import PreviewFrame from "../components/PreviewFrame.vue";
import type { Project } from "../types";

const route = useRoute();
const project = ref<Project | null>(null);
const files = ref<string[]>([]);
const error = ref("");
const projectId = String(route.params.id);

onMounted(async () => {
  try {
    [project.value, { files: files.value }] = await Promise.all([
      request<Project>(`/projects/${projectId}`),
      request<{ files: string[] }>(`/projects/${projectId}/files`),
    ]);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load the project";
  }
});
</script>

<template>
  <section v-if="project" class="space-y-5">
    <header class="card flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-semibold">{{ project.title }}</h1>
        <p class="mt-1 text-sm text-slate-400">{{ project.summary || "Project workspace" }}</p>
      </div>
      <div class="flex gap-3">
        <RouterLink class="btn-secondary" :to="`/chats/${project.chat_id}`">Open chat</RouterLink>
        <RouterLink class="btn-secondary" :to="`/chats/${project.chat_id}/history`">Run history</RouterLink>
      </div>
    </header>
    <p v-if="error" role="alert" class="card text-rose-300">{{ error }}</p>
    <div class="grid gap-5 lg:grid-cols-[1fr_2fr]">
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Project files</h2>
        <ul v-if="files.length" class="space-y-1 text-sm font-mono text-slate-300">
          <li v-for="file in files" :key="file">{{ file }}</li>
        </ul>
        <p v-else class="text-sm text-slate-400">No files yet.</p>
      </section>
      <section class="card space-y-3">
        <h2 class="text-lg font-semibold">Approved preview</h2>
        <PreviewFrame :project-id="project.id" />
      </section>
    </div>
  </section>
  <p v-else-if="error" role="alert" class="card text-rose-300">{{ error }}</p>
  <p v-else class="text-sm text-slate-400" role="status">Loading project…</p>
</template>
