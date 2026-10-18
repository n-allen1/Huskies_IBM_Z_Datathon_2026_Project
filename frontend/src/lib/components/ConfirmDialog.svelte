<script lang="ts">
  import type { Snippet } from 'svelte';

  let {
    open = $bindable(false),
    title,
    confirmText,
    busy = false,
    danger = false,
    onConfirm,
    children
  }: {
    open: boolean;
    title: string;
    confirmText: string;
    busy?: boolean;
    danger?: boolean;
    onConfirm: () => void;
    children: Snippet;
  } = $props();

  let dlg: HTMLDialogElement;

  // The native <dialog> gives us a focus trap, Escape-to-cancel and an inert background.
  $effect(() => {
    if (open && !dlg.open) dlg.showModal();
    if (!open && dlg.open) dlg.close();
  });
</script>

<dialog
  bind:this={dlg}
  onclose={() => (open = false)}
  aria-labelledby="cd-title"
  class="m-auto w-[min(26rem,calc(100vw-2rem))] rounded-lg border border-line-strong bg-panel p-0 text-fg backdrop:bg-black/60"
>
  <div class="p-4">
    <h2 id="cd-title" class="text-base font-semibold">{title}</h2>
    <div class="mt-2 text-sm text-muted">{@render children()}</div>
  </div>
  <div class="flex justify-end gap-2 border-t border-line bg-app/40 p-3">
    <!-- Cancel is the default-focused control so Enter/Space never fires the consequential action by accident. -->
    <!-- svelte-ignore a11y_autofocus -->
    <button class="btn" type="button" autofocus onclick={() => (open = false)}>Cancel</button>
    <button class="btn {danger ? 'btn-danger' : 'btn-primary'}" type="button" disabled={busy} onclick={onConfirm}>
      {busy ? 'Working…' : confirmText}
    </button>
  </div>
</dialog>
