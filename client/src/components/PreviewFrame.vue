<script setup lang="ts">
import { onMounted, ref } from "vue";
import { request } from "../api";

const props = defineProps<{ projectId: string }>();
const url = ref("");
const error = ref("");

onMounted(async () => {
  try {
    const data = await request<{ url: string }>(`/projects/${props.projectId}/preview-url`);
    url.value = data.url;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Preview unavailable";
  }
});
</script>

<template>
  <div>
    <p v-if="error" class="text-sm text-rose-300">{{ error }}</p>
    <iframe
      v-else-if="url"
      :src="url"
      title="Generated app preview"
      class="h-[28rem] w-full rounded-lg border border-slate-700 bg-white"
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      referrerpolicy="no-referrer"
    />
    <p v-else class="text-sm text-slate-400">Loading preview…</p>
  </div>
</template>
