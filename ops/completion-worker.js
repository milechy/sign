// Documenso の Webhook（DOCUMENT_COMPLETED）を受け、次を行う Cloudflare Worker。
//  1. 署名済み PDF の SHA-256 ハッシュ値を計算し、監査ログ PDF と一緒に、全員（お客様・ムーア）へメールで送る
//     （署名済み PDF 本体は、Documenso の完了メールがすでに全員に添付している。同じファイルのハッシュ）
//  2. Slack に「署名完了」を通知する（取引先・種別・金額は書類のタイトル「【種別】取引先_金額」から取る）
// 環境変数: WEBHOOK_SECRET, DOCUMENSO_URL（例 https://sign.mooores.com）, DOCUMENSO_API_TOKEN,
//           RESEND_API_KEY（送信専用）, MAIL_FROM（例 ムーア株式会社 <sign@mail.mooores.com>）, SLACK_WEBHOOK_URL
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
const b64 = (buf) => { let s = ""; const u = new Uint8Array(buf); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000)); return btoa(s); };

export async function handle(body, env) {
  const { event, payload } = body;
  if (event !== "DOCUMENT_COMPLETED") return "skip";
  const api = (p) => fetch(`${env.DOCUMENSO_URL}/api/v2${p}`, { headers: { Authorization: env.DOCUMENSO_API_TOKEN } }).then((r) => { if (!r.ok) throw new Error(`${p} ${r.status}`); return r; });
  const id = payload.envelopeId;
  const { envelopeItems } = await (await api(`/envelope/${id}`)).json();
  const lines = [];
  for (const it of envelopeItems) lines.push(`${it.title}.pdf  SHA-256: ${hex(await crypto.subtle.digest("SHA-256", await (await api(`/envelope/item/${it.id}/download?version=signed`)).arrayBuffer()))}`);
  const audit = await (await api(`/envelope/${id}/audit-log/download`)).arrayBuffer();
  const to = [...new Set(payload.recipients.map((r) => r.email))];
  const mail = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "content-type": "application/json" },
    body: JSON.stringify({
      from: env.MAIL_FROM, to,
      subject: `【署名完了】${payload.title} のハッシュ値と監査ログ`,
      text: `署名が完了しました。\n\n署名済みPDF（別メールの添付）のSHA-256ハッシュ値:\n${lines.join("\n")}\n\n監査ログ（いつ・だれが・どのIPから署名したか）を添付します。\n7年間保存してください。\n\nムーア株式会社`,
      attachments: [{ filename: `監査ログ_${payload.title}.pdf`, content: b64(audit) }],
    }),
  });
  if (!mail.ok) throw new Error(`resend ${mail.status}`);
  const [kind, rest = ""] = payload.title.split("】");
  const [client, amount = "-"] = rest.split("_");
  await fetch(env.SLACK_WEBHOOK_URL, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: `署名完了: ${client}｜${kind.replace("【", "")}｜金額 ${amount}\nハッシュ・監査ログは全員にメール済み` }) });
  return "sent";
}

export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("ok");
    if (req.headers.get("X-Documenso-Secret") !== env.WEBHOOK_SECRET) return new Response("no", { status: 401 });
    try { return new Response(await handle(await req.json(), env)); }
    catch (e) { return new Response(String(e), { status: 500 }); } // 500 を返すと Documenso が再送する
  },
};
