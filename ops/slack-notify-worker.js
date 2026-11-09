// Documenso Webhook → Slack 通知。Cloudflare Worker（または Node の fetch が使える関数）に置く。
// 環境変数: SLACK_WEBHOOK_URL（Slack の Incoming Webhook）, WEBHOOK_SECRET（Documenso の Webhook に設定した値）
// 書類のタイトルは「【種別】取引先_金額」にそろえる（例: 【個別・お試し】株式会社A_330000）。
// Webhook の中身に金額の欄は無いので、タイトルから取る。
export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("ok");
    // ヘッダー名は Documenso の「Webhook Verification」ページで確認すること（ops.md の未確認項目）
    if (req.headers.get("X-Documenso-Secret") !== env.WEBHOOK_SECRET) return new Response("no", { status: 401 });
    const { event, payload } = await req.json();
    if (event !== "DOCUMENT_COMPLETED") return new Response("skip");
    const [kind, rest = ""] = payload.title.split("】");
    const [client, amount = "-"] = rest.split("_");
    const text = `署名完了: ${client}｜${kind.replace("【", "")}｜金額 ${amount}\n${payload.title}`;
    await fetch(env.SLACK_WEBHOOK_URL, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
    return new Response("sent");
  },
};
