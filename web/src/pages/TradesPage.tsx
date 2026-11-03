// Trades list and trade screen. Placeholders for PLAN 6.1.
import { useParams } from 'react-router';
import { LargeTitleBar, PushedBar } from '../components';
import { useDispatchPreview, useTrades } from '../api/hooks';
import { useCurrentProject } from '../api/project';
import type { Trade } from '../api/types';
import { TRADE_LABEL, plural } from '../lib/labels';
import { ComingSoon, ErrorState, Loading } from './common';

export function TradesPage() {
  const { projectId } = useCurrentProject();
  const trades = useTrades(projectId);
  return (
    <>
      <LargeTitleBar title="Trades" />
      {trades.isLoading || !projectId ? (
        <Loading label="Loading trades" />
      ) : trades.isError ? (
        <ErrorState error={trades.error} onRetry={() => trades.refetch()} />
      ) : (
        <ComingSoon task="6.1" what={`Each trade with its company and open items. ${plural(trades.data?.trades.length ?? 0, 'trade')}.`} />
      )}
    </>
  );
}

export function TradePage() {
  const { trade } = useParams() as { trade: Trade };
  const { projectId } = useCurrentProject();
  const preview = useDispatchPreview(projectId, trade);
  const company = preview.data?.contact?.company ?? TRADE_LABEL[trade] ?? 'Trade';
  const open = preview.data?.lines.length;
  return (
    <>
      <PushedBar
        backTo="/trades"
        backLabel="Trades"
        title={company}
        subtitle={open != null ? `${TRADE_LABEL[trade]}. ${plural(open, 'open item')}.` : undefined}
      />
      {preview.isLoading || !projectId ? (
        <Loading label="Loading the message preview" />
      ) : preview.isError ? (
        <ErrorState error={preview.error} onRetry={() => preview.refetch()} />
      ) : (
        <ComingSoon task="6.1" what="The message preview exactly as it will be sent, and sent history." />
      )}
    </>
  );
}
