import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BrandMark } from '@/components/ui/BrandMark';

describe('正式品牌图形', () => {
  it('使用有明确尺寸的同源正式资产并保持装饰语义', () => {
    const { container } = render(<BrandMark size={28} />);
    const mark = container.querySelector('img')!;
    expect(mark).toHaveAttribute('src', '/brand/aragon-mark-128.png');
    expect(mark).toHaveAttribute('width', '28');
    expect(mark).toHaveAttribute('height', '28');
    expect(mark).toHaveAttribute('alt', '');
    expect(mark).toHaveAttribute('aria-hidden', 'true');
  });
});
