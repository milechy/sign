// 実行: node --test ops/completion-worker.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { handle } from "./completion-worker.js";

test("署名済みPDFのSHA-256を全員宛のメールに入れ、監査ログを添付する", async () => {
  const pdf = Buffer.from("signed-pdf-bytes"), audit = Buffer.from("audit-bytes"), sent = [];
  globalThis.fetch = async (url, opts) => {
    const u = String(url);
    if (u.endsWith("/envelope/E1")) return Response.json({ envelopeItems: [{ id: "I1", title: "契約" }] });
    if (u.includes("/envelope/item/I1/download?version=signed")) return new Response(pdf);
    if (u.endsWith("/audit-log/download")) return new Response(audit);
    sent.push([u, JSON.parse(opts.body)]); return new Response("{}");
  };
  const env = { DOCUMENSO_URL: "https://x", DOCUMENSO_API_TOKEN: "t", RESEND_API_KEY: "r", MAIL_FROM: "a@b", SLACK_WEBHOOK_URL: "https://slack" };
  const r = await handle({ event: "DOCUMENT_COMPLETED", payload: { envelopeId: "E1", title: "【個別・お試し】株式会社A_330000", recipients: [{ email: "c@x" }, { email: "m@x" }, { email: "c@x" }] } }, env);
  assert.equal(r, "sent");
  const mail = sent.find(([u]) => u.includes("resend"))[1];
  assert.deepEqual(mail.to, ["c@x", "m@x"]);
  assert.ok(mail.text.includes(createHash("sha256").update(pdf).digest("hex")));
  assert.equal(Buffer.from(mail.attachments[0].content, "base64").toString(), "audit-bytes");
  assert.ok(sent.find(([u]) => u === "https://slack")[1].text.includes("株式会社A"));
  assert.equal(await handle({ event: "DOCUMENT_CREATED", payload: {} }, env), "skip");
});
