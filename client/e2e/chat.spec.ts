import { expect, test } from "@playwright/test";

type MockMessage = {
  id: string;
  chat_id: string;
  run_id: string | null;
  role: "user" | "assistant";
  content: string;
  sequence: number;
  run_status: string | null;
  created_at: string;
};

test("project chats keep their runs, history, and context across switching and reload", async ({ page }) => {
  const chats = new Map<string, { id: string; project_id: string; title: string; updated_at: string }>();
  const messages = new Map<string, MockMessage[]>();
  const projects = new Map<string, { id: string; chat_id: string; title: string; preview_id: string }>();
  const runs = new Map<string, Record<string, unknown>>();
  const artifacts = new Map<string, Record<string, unknown>[]>();
  const runEvents = new Map<string, Record<string, unknown>[]>();
  const idempotentResponses = new Map<string, unknown>();
  const titleSuggestions = new Map<string, string>();
  let chatNumber = 0;
  let runNumber = 0;
  let messageNumber = 0;
  let lastMessageProjectId: string | null = null;

  const addMessage = (chatId: string, content: string, role: "user" | "assistant", runId: string | null) => {
    const message: MockMessage = {
      id: `message-${++messageNumber}`,
      chat_id: chatId,
      run_id: runId,
      role,
      content,
      sequence: (messages.get(chatId)?.length ?? 0) + 1,
      run_status: runId ? (runs.get(runId)?.status as string) : null,
      created_at: "2026-10-23T12:00:00Z",
    };
    messages.get(chatId)?.push(message);
  };

  const createRun = (
    chatId: string,
    content: string,
    status: "completed" | "awaiting_user",
    idempotencyKey: string,
  ) => {
    const chat = chats.get(chatId)!;
    const runId = `run-${++runNumber}`;
    const executionId = `execution-${runNumber}-1`;
    const reply =
      status === "completed"
        ? "Your changes are ready.\n\nThe result looks good.\n\nI updated 1 file. File checks and required JavaScript syntax checks passed."
        : "I’ve made the requested changes, but I need your guidance before I can finish.\n\nThe request needs clarification.\n\nI updated 1 file. Your last approved preview is unchanged.";
    const run = {
      id: runId,
      chat_id: chatId,
      project_id: chat.project_id,
      execution_id: executionId,
      message: content,
      status,
      user_guidance: null,
      plan: {
        id: `plan-${runId}`,
        project_id: chat.project_id,
        summary: `Plan for ${content}`,
        steps: [
          {
            id: `step-${runId}`,
            name: "Project page",
            file_path: "index.html",
            file_type: "html",
            description: "Build the project page.",
          },
        ],
      },
      latest_code: [
        {
          file_path: "index.html",
          content: "<h1>Generated app</h1>",
          checksum: "a".repeat(64),
          file_type: "html",
        },
      ],
      feedback: {
        verdict: status === "completed" ? "approve" : "escalate",
        summary: status === "completed" ? "Looks good." : "Needs guidance.",
        issues: [],
      },
      checks: {
        file_structure_ok: true,
        js_syntax_ok: true,
        files_checked: 1,
        syntax_checked: [],
        issues: [],
      },
      chat_reply: reply,
      error: null,
      created_at: "2026-10-23T12:00:00Z",
      updated_at: "2026-10-23T12:00:00Z",
    };
    runs.set(runId, run);
    artifacts.set(runId, [
      {
        id: `artifact-${runId}-1`,
        execution_id: executionId,
        file_path: "index.html",
        file_type: "html",
        checksum: "a".repeat(64),
        created_at: "2026-10-23T12:00:02Z",
      },
    ]);
    runEvents.set(runId, [
      {
        id: `event-${runId}-1`,
        sequence: 1,
        execution_id: executionId,
        kind: "step_completed",
        payload: { step_key: "planner", summary: "Created a one-file plan." },
        created_at: "2026-10-23T12:00:01Z",
      },
    ]);
    addMessage(chatId, content, "user", runId);
    addMessage(chatId, reply, "assistant", runId);
    chat.updated_at = "2026-10-23T12:00:02Z";
    idempotentResponses.set(idempotencyKey, run);
    return run;
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const key = request.headers()["idempotency-key"];
    const reply = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/auth/login" && method === "POST") {
      await reply({ access_token: "browser-test-token", token_type: "bearer" });
    } else if (path === "/users/me") {
      await reply({ id: "user-1", email: "browser@example.com" });
    } else if (path === "/chats" && method === "GET") {
      await reply([...chats.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
    } else if (path === "/chats" && method === "POST") {
      const body = request.postDataJSON() as { message: string };
      const chatId = `chat-${++chatNumber}`;
      const projectId = `project-${chatNumber}`;
      const title = body.message.slice(0, 80);
      chats.set(chatId, { id: chatId, project_id: projectId, title, updated_at: "2026-10-23T12:00:00Z" });
      projects.set(projectId, { id: projectId, chat_id: chatId, title, preview_id: `preview-${chatId}` });
      messages.set(chatId, []);
      titleSuggestions.set(
        chatId,
        body.message.toLowerCase().includes("coupon")
          ? "Coupon validator"
          : body.message.toLowerCase().includes("flappy")
            ? "Flappy Bird"
            : "New project",
      );
      const run = createRun(chatId, body.message, "completed", key ?? "create-key");
      await reply({ chat_id: chatId, project_id: projectId, run_id: run.id }, 201);
    } else if (path.startsWith("/chats/") && path.endsWith("/messages") && method === "GET") {
      const chatId = path.split("/")[2];
      await reply(
        (messages.get(chatId) ?? []).map((message) => ({
          ...message,
          run_status: message.run_id ? runs.get(message.run_id)?.status ?? null : null,
        })),
      );
    } else if (path.startsWith("/chats/") && path.endsWith("/messages") && method === "POST") {
      const chatId = path.split("/")[2];
      const duplicate = key ? idempotentResponses.get(key) : undefined;
      if (duplicate) {
        const run = duplicate as { id: string; status: string };
        await reply({ message_id: "existing-message", run_id: run.id, status: run.status }, 200);
        return;
      }
      const body = request.postDataJSON() as { message: string };
      const chat = chats.get(chatId);
      if (!chat) {
        await reply({ detail: "Not found" }, 404);
        return;
      }
      lastMessageProjectId = chat.project_id;
      const run = createRun(
        chatId,
        body.message,
        body.message === "Add a score counter" ? "awaiting_user" : "completed",
        key ?? `message-${messageNumber}`,
      );
      await reply({ message_id: `message-${messageNumber}`, run_id: run.id, status: run.status }, 202);
    } else if (path.startsWith("/chats/") && path.endsWith("/runs")) {
      const chatId = path.split("/")[2];
      await reply([...runs.values()].filter((run) => run.chat_id === chatId).reverse());
    } else if (path.startsWith("/chats/") && method === "GET") {
      const chat = chats.get(path.split("/")[2]);
      const suggestedTitle = titleSuggestions.get(chat?.id ?? "");
      if (chat && suggestedTitle) {
        chat.title = suggestedTitle;
        projects.get(chat.project_id)!.title = suggestedTitle;
        titleSuggestions.delete(chat.id);
      }
      await reply(chat ?? { detail: "Not found" }, chat ? 200 : 404);
    } else if (path.startsWith("/chats/") && method === "PATCH") {
      const chat = chats.get(path.split("/")[2]);
      if (chat) {
        chat.title = (request.postDataJSON() as { title: string }).title;
        projects.get(chat.project_id)!.title = chat.title;
      }
      await reply(chat ?? { detail: "Not found" }, chat ? 200 : 404);
    } else if (/^\/runs\/[^/]+\/artifacts$/.test(path)) {
      await reply(artifacts.get(path.split("/")[2]) ?? []);
    } else if (/^\/runs\/[^/]+\/artifacts\/[^/]+$/.test(path)) {
      await reply({ content: "<h1>Generated app</h1>" });
    } else if (/^\/runs\/[^/]+\/events$/.test(path)) {
      await reply(runEvents.get(path.split("/")[2]) ?? []);
    } else if (/^\/runs\/[^/]+\/continuations$/.test(path) && method === "POST") {
      const runId = path.split("/")[2];
      const run = runs.get(runId);
      if (!run || run.status !== "awaiting_user") {
        await reply({ detail: "Only awaiting_user runs can be continued" }, 409);
        return;
      }
      const existing = key ? idempotentResponses.get(key) : undefined;
      if (existing) {
        await reply(existing, 200);
        return;
      }
      const body = request.postDataJSON() as { guidance: string };
      run.execution_id = `execution-${runId}-2`;
      run.user_guidance = body.guidance;
      run.status = "completed";
      run.chat_reply = "Your changes are ready. Guidance has been applied and the run is complete.";
      artifacts.get(runId)?.push({
        id: `artifact-${runId}-2`,
        execution_id: run.execution_id,
        file_path: "index.html",
        file_type: "html",
        checksum: "b".repeat(64),
        created_at: "2026-10-23T12:00:05Z",
      });
      addMessage(run.chat_id as string, body.guidance, "user", runId);
      addMessage(run.chat_id as string, run.chat_reply as string, "assistant", runId);
      idempotentResponses.set(key ?? "continuation-key", run);
      await reply(run);
    } else if (/^\/runs\/[^/]+\/close$/.test(path) && method === "POST") {
      const run = runs.get(path.split("/")[2]);
      if (run) run.status = "failed";
      await reply(run ?? { detail: "Not found" }, run ? 200 : 404);
    } else if (/^\/runs\/[^/]+$/.test(path)) {
      const run = runs.get(path.split("/")[2]);
      await reply(run ?? { detail: "Not found" }, run ? 200 : 404);
    } else if (path.startsWith("/projects/") && path.endsWith("/preview-url")) {
      const projectId = path.split("/")[2];
      await reply({ url: `https://preview.example.test/preview/${projectId}/index.html` });
    } else if (path.startsWith("/projects/") && path.endsWith("/files")) {
      await reply({ files: ["index.html"] });
    } else if (path.startsWith("/projects/") && method === "GET") {
      const project = projects.get(path.split("/")[2]);
      await reply(
        project
          ? { ...project, summary: "", preview_ready: true, status: "active", created_at: "2026-10-23T12:00:00Z", updated_at: "2026-10-23T12:00:00Z" }
          : { detail: "Not found" },
        project ? 200 : 404,
      );
    } else {
      await reply({ detail: `Unhandled browser-test request: ${method} ${path}` }, 404);
    }
  });
  await page.context().route("https://preview.example.test/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<!doctype html><html><body><h1>Generated preview</h1></body></html>",
    }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill("browser@example.com");
  await page.getByLabel("Password").fill("test-password");
  await page.locator("form").getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator("#app-sidebar img")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "AI Coding Multi-Agent home" })).toBeVisible();
  await expect(page.getByText("browser@example.com")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.getByRole("button", { name: "New chat" }).last().click();
  await page.getByRole("textbox", { name: "Describe your project" }).fill("Build a coupon validator");
  await page.getByRole("button", { name: "Create project and start" }).click();
  await expect(page.getByText("Your changes are ready.", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Coupon validator" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Coupon validator" })).toBeVisible();
  await page.getByRole("button", { name: "Options for Coupon validator" }).click();
  await page.getByRole("menuitem", { name: "Rename chat" }).click();
  await page.getByRole("textbox", { name: "Rename chat" }).fill("Coupon app");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("link", { name: "Coupon app" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Coupon app" })).toBeVisible();

  await page.getByRole("link", { name: "View details" }).first().click();
  await expect(page.getByText("index.html", { exact: true }).first()).toBeVisible();
  await expect(page.locator('iframe[title="Generated app preview"]')).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Coupon app" })).toBeVisible();
  await expect(page.getByText("run-1", { exact: true })).toBeVisible();
  await expect(page.getByText("Selected run", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open latest approved preview" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("region", { name: "Files" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Event timeline" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.setViewportSize({ width: 1440, height: 900 });
  const [previewPage] = await Promise.all([
    page.context().waitForEvent("page"),
    page.getByRole("link", { name: "Open latest approved preview" }).click(),
  ]);
  await expect(previewPage.getByRole("heading", { name: "Generated preview" })).toBeVisible();
  await previewPage.close();
  await page.getByRole("link", { name: "Back to chat" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("textbox", { name: "Message" })).toBeVisible();
  await expect(page.getByRole("link", { name: "View details" }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.getByRole("button", { name: "Open sidebar" }).click();
  await expect(page.getByRole("link", { name: "Coupon app" })).toBeVisible();
  await page.getByRole("button", { name: "Close sidebar" }).click();
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.getByRole("textbox", { name: "Message" }).fill("Add a score counter");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Handoff — needs you").last()).toBeVisible();
  await page.getByRole("link", { name: "View details" }).last().click();
  await expect(
    page.getByRole("heading", { name: "This request needs your guidance" }),
  ).toBeVisible();
  await page.getByPlaceholder("What should change?").fill("Keep the layout and add the counter");
  await page.getByRole("button", { name: "Continue with guidance" }).click();
  await expect(page.getByText("Completed", { exact: true })).toBeVisible();
  const savedExecution = page.getByLabel("Saved execution");
  await expect(savedExecution.locator("option")).toHaveCount(3);
  await savedExecution.selectOption({ label: "Execution 1" });
  await expect(
    page.getByRole("region", { name: "Files" }).getByRole("button", { name: "index.html" }),
  ).toHaveCount(1);
  await savedExecution.selectOption({ label: "Execution 2" });

  await page.getByRole("button", { name: "New chat" }).last().click();
  await page.getByRole("textbox", { name: "Describe your project" }).fill("Build Flappy Bird");
  await page.getByRole("button", { name: "Create project and start" }).click();
  await expect(page.getByText("Your changes are ready.", { exact: false }).last()).toBeVisible();
  await page.getByRole("link", { name: "Coupon app" }).click();
  await page.getByRole("textbox", { name: "Message" }).fill("Add a high-score display");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Add a high-score display")).toBeVisible();
  expect(lastMessageProjectId).toBe("project-1");

  await page.reload();
  await expect(page.getByText("Build a coupon validator").first()).toBeVisible();
  await expect(page.getByText("Keep the layout and add the counter")).toBeVisible();
  await expect(page.getByText("Add a high-score display")).toBeVisible();
  await page.getByRole("link", { name: "View details" }).first().click();
  await expect(page.getByRole("heading", { name: "Coupon app" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open latest approved preview" })).toBeVisible();
  await page.getByRole("link", { name: "Back to chat" }).click();
  await page.getByRole("link", { name: "Run history" }).click();
  await expect(page.getByRole("heading", { name: "Run history" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Runs" }).getByRole("listitem")).toHaveCount(3);

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByRole("button", { name: "Open sidebar" })).toBeVisible();
  await expect(page.locator("#app-sidebar img")).toHaveAttribute("src", "/favicon.svg?v=2");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440);
  await page.getByRole("button", { name: "Open sidebar" }).click();
  await expect(page.getByRole("button", { name: "Open sidebar" })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open sidebar" }).click();
  await expect(page.getByRole("button", { name: "Open sidebar" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Close sidebar" })).toBeVisible();
  await page.getByRole("button", { name: "Close sidebar" }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.goto("/projects/project-1");
  await expect(page.getByRole("heading", { name: "Coupon app" })).toBeVisible();
});
