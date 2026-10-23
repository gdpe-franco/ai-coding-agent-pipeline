<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();
const mode = ref<"login" | "register">("login");
const email = ref("");
const password = ref("");
const localError = ref("");

async function submit() {
  localError.value = "";
  try {
    if (mode.value === "login") {
      await auth.login(email.value, password.value);
    } else {
      await auth.register(email.value, password.value);
    }
    await router.push("/chats");
  } catch {
    localError.value = auth.error || "Authentication failed";
  }
}
</script>

<template>
  <div class="mx-auto mt-16 max-w-md">
    <div class="card space-y-6 p-6">
      <div class="space-y-2">
        <p class="mono-label">AI coding workspace</p>
        <h1 class="text-3xl font-semibold">Plan, build, review.</h1>
        <p class="text-sm leading-6 text-muted">
          Describe a static web app; the planner, coder, and reviewer do the rest.
        </p>
      </div>
      <div class="flex gap-2">
        <button
          class="btn-secondary flex-1"
          :class="mode === 'login' ? 'border-accent bg-raised text-paper' : ''"
          @click="mode = 'login'"
        >
          Sign in
        </button>
        <button
          class="btn-secondary flex-1"
          :class="mode === 'register' ? 'border-accent bg-raised text-paper' : ''"
          @click="mode = 'register'"
        >
          Register
        </button>
      </div>
      <form class="space-y-3" @submit.prevent="submit">
        <label class="block text-sm">
          <span class="mb-1 block text-muted">Email</span>
          <input v-model="email" class="input" type="email" autocomplete="email" required />
        </label>
        <label class="block text-sm">
          <span class="mb-1 block text-muted">Password</span>
          <input v-model="password" class="input" type="password" minlength="8" autocomplete="current-password" required />
        </label>
        <p v-if="localError" class="text-sm text-danger">{{ localError }}</p>
        <button class="btn w-full" type="submit" :disabled="auth.loading">
          {{ mode === "login" ? "Sign in" : "Create account" }}
        </button>
      </form>
    </div>
  </div>
</template>
