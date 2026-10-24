<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { request } from "../api";
import type { Project, Run } from "../types";

const route = useRoute();
const router = useRouter();
const error = ref("");

onMounted(async () => {
  try {
    const project = await request<Project>(`/projects/${String(route.params.id)}`);
    const runs = await request<Run[]>(`/chats/${project.chat_id}/runs`);
    await router.replace(runs[0] ? `/runs/${runs[0].id}` : `/chats/${project.chat_id}`);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Could not open this project's latest run";
  }
});
</script>

<template>
  <p v-if="error" class="card text-danger" role="alert">{{ error }}</p>
  <p v-else class="text-sm text-muted" role="status">Opening the latest project run…</p>
</template>
