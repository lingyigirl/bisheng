type Patch<T> = Partial<T> | ((current: T) => Partial<T>);

/** Commit one immutable projection per interval, using the latest store value. */
export function createViewBatcher<T>(commit: (update: (current: T) => Partial<T>) => void) {
    let patches: Patch<T>[] = [];
    let timer: ReturnType<typeof setTimeout> | null = null;
    const flush = () => {
        if (timer) clearTimeout(timer);
        timer = null;
        if (!patches.length) return;
        const pending = patches;
        patches = [];
        commit(current => pending.reduce<T>((state, patch) => ({
            ...state, ...(typeof patch === 'function' ? patch(state) : patch),
        }), current));
    };
    return {
        enqueue(patch: Patch<T>) {
            patches.push(patch);
            if (patches.length >= 128) flush();
            else timer ??= setTimeout(flush, 100);
        },
        flush,
    };
}

const socketFlushes = new WeakMap<WebSocket, () => void>();
export function registerViewFlush(socket: WebSocket, flush: () => void) {
    socketFlushes.set(socket, flush);
}
export function flushSocketView(socket?: WebSocket) {
    if (socket) socketFlushes.get(socket)?.();
}
