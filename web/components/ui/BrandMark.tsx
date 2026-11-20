import Image from 'next/image';

/** 同源正式品牌图形；相邻文字或外层链接提供可访问名。 */
export function BrandMark({ size = 24 }: { size?: 24 | 28 }) {
  return (
    <Image
      src="/brand/aragon-mark-128.png"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      unoptimized
      className="shrink-0"
    />
  );
}
