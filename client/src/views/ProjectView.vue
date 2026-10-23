<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { request } from "../api";
import type { Project } from "../types";

const route = useRoute();
const project = ref<Project | null>(null);
const previewUrl = ref("");
const error = ref("");
const projectId = String(route.params.id);

onMounted(async () => {
  try {
    project.value = await request<Project>(`/projects/${projectId}`);
    if (project.value.preview_ready) {
      previewUrl.value = (await request<{ url: string }>(`/projects/${projectId}/preview-url`)).url;
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not load the project";
  }
});
</script>

<template>
  <section v-if="project" class="card mx-auto max-w-3xl space-y-4">
    <div>
      <h1 class="text-2xl font-semibold">{{ project.title }}</h1>
      <p class="mt-2 whitespace-pre-wrap text-sm text-slate-400">
        {{ project.summary || "Your project workspace." }}
      </p>
    </div>
    <a
      v-if="previewUrl"
      class="btn inline-flex"
      :href="previewUrl"
      target="_blank"
      rel="noopener noreferrer"
    >
      Open approved preview ↗
    </a>
    <p v-else class="text-sm text-slate-500">No approved preview yet.</p>
  </section>
  <p v-else-if="error" role="alert" class="card text-rose-300">{{ error }}</p>
  <p v-else class="text-sm text-slate-400" role="status">Loading project…</p>
</template>
