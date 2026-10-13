import { Box } from 'lucide-react';

export function Brand({ inverted = false }: { inverted?: boolean }) {
  return (
    <span className={`brand${inverted ? ' brand-inverted' : ''}`}>
      <span className="brand-glyph">
        <Box size={25} strokeWidth={1.7} aria-hidden="true" />
      </span>
      <span className="brand-wordmark">
        <strong>识物说</strong>
        <small>ITEMTALK</small>
      </span>
    </span>
  );
}
