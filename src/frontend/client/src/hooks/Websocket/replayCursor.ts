export function isNewerEvent(incoming: string, current?: string): boolean {
    if (!/^\d+-\d+$/.test(incoming)) return false;
    if (!current) return true;
    const left = incoming.split('-').map(BigInt);
    const right = current.split('-').map(BigInt);
    return left[0] > right[0] || (left[0] === right[0] && left[1] > right[1]);
}
