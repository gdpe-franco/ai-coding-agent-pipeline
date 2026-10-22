import { defineStore } from "pinia";
import { login as loginRequest, register as registerRequest, request, setToken, getToken } from "../api";
import type { User } from "../types";

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: getToken(),
    user: null as User | null,
    loading: false,
    error: "",
  }),
  getters: {
    authenticated: (state) => Boolean(state.token),
  },
  actions: {
    async login(email: string, password: string) {
      this.loading = true;
      this.error = "";
      try {
        this.token = await loginRequest(email, password);
        setToken(this.token);
        await this.fetchMe();
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Login failed";
        throw error;
      } finally {
        this.loading = false;
      }
    },
    async register(email: string, password: string) {
      this.loading = true;
      this.error = "";
      try {
        await registerRequest(email, password);
        await this.login(email, password);
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Registration failed";
        throw error;
      } finally {
        this.loading = false;
      }
    },
    async fetchMe() {
      if (!this.token) {
        return;
      }
      try {
        this.user = await request<User>("/users/me");
      } catch {
        this.logout();
      }
    },
    logout() {
      this.token = null;
      this.user = null;
      setToken(null);
    },
  },
});
