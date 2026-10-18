<script lang="ts">
  import { PRIORITY, prioritySvg, type Priority } from '$lib/ops';

  let { priority, confirmed = true, size = 18 }: { priority: Priority; confirmed?: boolean; size?: number } = $props();
  const meta = $derived(PRIORITY[priority]);
</script>

<!-- Shape + numeral + text: severity never relies on color alone. -->
<span class="inline-flex items-center gap-1.5" title={meta.name + (confirmed ? '' : ' (not yet confirmed by a dispatcher)')}>
  {@html prioritySvg(priority, size)}
  <span class="text-xs font-bold" style="color: {meta.color}">{priority === 'unassigned' ? 'No priority' : priority}</span>
  {#if !confirmed && priority !== 'unassigned'}<span class="text-[0.65rem] font-medium text-muted">unconfirmed</span>{/if}
</span>
