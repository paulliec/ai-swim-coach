export default {
  async scheduled(event, env, ctx) {
    const url = `${env.BACKEND_URL}/health`;
    const resp = await fetch(url, { method: "GET" });

    if (!resp.ok) {
      console.error(`keep-warm failed: ${resp.status} ${resp.statusText}`);
    }
  },
};
