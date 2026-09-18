import { apiUrl } from "./api.js";

// Reattach to backend-owned work after a transport interruption. Never retry the
// POST: it launches work. Sequence numbers resume delivery without duplicates.
export async function fetchChatTurn(body, { signal, fetchImpl = fetch } = {}) {
  const reconnect = after => {
    const query = new URLSearchParams({ conversationId: body.conversationId, turnId: body.turnId, after: String(after) });
    return fetchImpl(apiUrl(`/chat/events?${query}`), { signal });
  };
  let response;
  try {
    response = await fetchImpl(apiUrl("/chat/stream"), {
      method: "POST", headers: { "Content-Type": "application/json" }, signal,
      body: JSON.stringify(body),
    });
  } catch (error) {
    if (signal?.aborted || !body.conversationId || !body.turnId) throw error;
    // The server may have launched the turn before its response was lost.
    response = await reconnect(0);
  }
  if (!response.ok || !response.body) return response;
  const encoder = new TextEncoder();
  let reader = response.body.getReader();
  let sequence = 0;
  let terminal = false;
  let cancelled = false;
  return new Response(new ReadableStream({
    async start(controller) {
      try {
        for (let attempt = 0; ; attempt++) {
          const decoder = new TextDecoder();
          let buffer = "";
          try {
            while (!cancelled) {
              const { done, value } = await reader.read();
              buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
              if (done && buffer) buffer += "\n";
              const lines = buffer.split("\n");
              buffer = lines.pop();
              const delivered = [];
              let nextSequence = sequence;
              let nextTerminal = terminal;
              for (const line of lines) {
                if (!line.trim()) continue;
                const event = JSON.parse(line);
                if (event.sequence && event.sequence <= nextSequence) continue;
                nextSequence = event.sequence || nextSequence;
                nextTerminal = ["final", "error", "stopped"].includes(event.type);
                delivered.push(line);
              }
              // Keep each network batch together so persistence can commit it once.
              if (delivered.length) controller.enqueue(encoder.encode(`${delivered.join("\n")}\n`));
              sequence = nextSequence;
              terminal = nextTerminal;
              if (done) break;
            }
            if (terminal || cancelled) break;
          } catch (error) {
            if (signal?.aborted || cancelled) throw error;
          } finally {
            await reader.cancel?.()?.catch(() => {});
          }
          if (attempt >= 3) throw new Error("Connection to Rem was lost. The turn continues in the background.");
          signal?.throwIfAborted();
          const resumed = await reconnect(sequence);
          if (!resumed.ok || !resumed.body) throw new Error(`Cannot reconnect to Rem (${resumed.status})`);
          reader = resumed.body.getReader();
        }
        if (!cancelled) controller.close();
      } catch (error) {
        if (!cancelled) controller.error(error);
      }
    },
    cancel() { cancelled = true; return reader.cancel?.(); },
  }), { headers: response.headers, status: response.status });
}
