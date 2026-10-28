import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SolutionBriefing } from '../SolutionBriefing';
import templateText from '../../../../../meridian_agentcore/agentcore/agentcore.template.json?raw';
import { POLICIES, TOOLS, toolCounts, toolsHeading } from '../solutionBriefingContent';

describe('SolutionBriefing', () => {
  it('starts with only architecture open and reveals preparation and phases on demand', () => {
    const { container } = render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    expect(container.querySelectorAll('details[open]')).toHaveLength(1);
    expect(screen.getByText('The architecture').closest('details')).toHaveAttribute('open');
    expect(screen.getByText('Package facts')).not.toBeVisible();
    expect(screen.getByText('Query vector → pgvector')).not.toBeVisible();
    fireEvent.click(screen.getByText('Prepare the data'));
    fireEvent.click(screen.getByText('Build five capabilities'));
    expect(screen.getByRole('heading', { name: 'Prepare the data' })).toBeVisible();
    for (const name of ['Phase 1 - SQL', 'Phase 2 - MCP', 'Phase 3 - Retrieval', 'Phase 4 - Production', 'Phase 5 - Workflow']) {
      expect(screen.getByRole('heading', { name })).toBeVisible();
    }
    expect(screen.getByText('Query vector → pgvector')).not.toBeVisible();
    fireEvent.click(screen.getByRole('heading', { name: 'Phase 3 - Retrieval' }));
    expect(screen.getByText('Query vector → pgvector')).toBeVisible();
    expect(screen.getByText('Query text → tsvector')).toBeVisible();
    expect(screen.getByText('Merge candidates by package ID')).toBeVisible();
    expect(screen.getByText('Illustrative query, seeded catalog example')).toBeVisible();
    expect(screen.getByRole('img', { name: 'Green rice terraces and palms in Bali' })).toHaveAttribute('src', '/travel/catalog/BCH-003.jpg');
    expect(screen.getByText('Prepare hold', { exact: true })).not.toBeVisible();
    fireEvent.click(screen.getByRole('heading', { name: 'Phase 5 - Workflow' }));
    expect(screen.getByText('Prepare hold', { exact: true })).toBeVisible();
    expect(screen.getByText('Save the write intent')).toBeVisible();
  });

  it('states how access is checked without naming a signing scheme', () => {
    const { container } = render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    const architecture = container.querySelector('.mds-brief-architecture')?.textContent ?? '';
    expect(architecture).toContain('Access checked on every call');
    expect(architecture).toContain('Policy: Cedar checks');
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/IAM|SigV4|signed MCP|shared token|workload identity/i);
    expect(screen.getByText('Check access at every hop')).toBeInTheDocument();
    expect(screen.getByText(/each check the caller before acting, and the database applies its own row-level security/))
      .toBeInTheDocument();
  });

  it('reveals policy details only after opening the verification section', () => {
    render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    expect(screen.getByText('Tool contracts & Cedar policies')).not.toBeVisible();
    fireEvent.click(screen.getByText('Verify the boundaries'));
    expect(screen.getByText('Tool contracts & Cedar policies')).toBeVisible();
    expect(screen.getByText('meridian_hold_governance')).not.toBeVisible();
    fireEvent.click(screen.getByText('Tool contracts & Cedar policies'));
    expect(screen.getByText('meridian_hold_governance')).toBeVisible();
    expect(screen.getByText('meridian_booking_governance')).toBeVisible();
    expect(screen.getByText('MeridianHolds___create_courtesy_hold')).toBeVisible();
    expect(screen.getByText('MeridianHolds___confirm_booking')).toBeVisible();
    expect(screen.getAllByText(/context\.input\.totalCents <= context\.input\.budgetCeilingCents/)).toHaveLength(2);
    fireEvent.click(screen.getByText('Tool contracts & Cedar policies'));
    expect(screen.getByText('meridian_hold_governance')).not.toBeVisible();
  });

  it('preserves preparation and distinct recovery failure windows in the reference', () => {
    render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    fireEvent.click(screen.getByText('Verify the boundaries'));
    fireEvent.click(screen.getByText('Implementation reference'));
    expect(screen.getByText(/seed_data.py loads Aurora/)).toBeVisible();
    fireEvent.click(screen.getByText('Recovery guarantees & evidence'));
    for (const text of ['Before the hold', 'Write committed, response lost', 'After the hold step is saved']) {
      expect(screen.getByText(text)).toBeVisible();
    }
    expect(screen.getByText(/separate transactions/)).toBeVisible();
  });

  it('hands off to the capability ladder and live evidence', () => {
    const onOpenLadder = vi.fn();
    const onOpenEvidence = vi.fn();
    render(<SolutionBriefing onOpenLadder={onOpenLadder} onOpenEvidence={onOpenEvidence} />);
    fireEvent.click(screen.getByRole('button', { name: /Open the capability ladder/ }));
    expect(onOpenLadder).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /Inspect system evidence/ }));
    expect(onOpenEvidence).toHaveBeenCalledTimes(1);
  });
});

describe('SolutionBriefing against the deployed Cedar template', () => {
  const squash = (text: string) => text.replace(/\s+/g, ' ').replace(/\(\s+/g, '(').replace(/\[\s+/g, '[')
    .replace(/\s+\]/g, ']').replace(/\s+\)/g, ')').trim();
  const generic = (text: string) => text
    .replace('{{AWS_REGION}}', '<region>').replace('{{AWS_ACCOUNT_ID}}', '<account-id>')
    .replace('{{GATEWAY_ID}}', '<gateway-id>');
  const template = JSON.parse(templateText);
  const deployed = template.policyEngines[0].policies as { name: string; statement: string }[];
  const gatewayTargets = (template.agentCoreGateways[0].targets as { name: string }[]).map(target => target.name);

  it('shows every deployed Cedar statement, read rule and traveler binding included', () => {
    const shown = new Map(POLICIES.map(([name, , statement]) => [name, squash(statement)]));
    expect([...shown.keys()].sort()).toEqual(deployed.map(policy => policy.name).sort());
    for (const policy of deployed) {
      expect(shown.get(policy.name), policy.name).toBe(squash(generic(policy.statement)));
    }
  });

  it('lists the tools the Gateway serves, the two writes named here, and none that were retired', () => {
    const listed = TOOLS.map(([name]) => name).sort();
    const read = deployed.find(policy => policy.name === 'meridian_read_tools')!.statement;
    const readActions = [...read.matchAll(/Action::"([^"]+)"/g)].map(match => match[1]);
    const writes = ['MeridianHolds___create_courtesy_hold', 'MeridianHolds___confirm_booking'];
    expect(listed).toEqual([...readActions, ...writes].sort());
  });

  it('groups the listed tools under exactly the targets the template defines', () => {
    const prefixes = new Set(TOOLS.map(([name]) => name.split('___')[0]));
    expect([...prefixes].sort()).toEqual([...gatewayTargets].sort());
  });

  it('counts the tools and targets in the heading from the same data the list renders', () => {
    const { container } = render(<SolutionBriefing onOpenLadder={() => {}} onOpenEvidence={() => {}} />);
    const heading = [...container.querySelectorAll('h3')].find(node => /MCP tools/.test(node.textContent ?? ''));
    expect(heading?.textContent).toBe(toolsHeading());
    expect(heading?.textContent).toBe('Ten MCP tools, two Lambda targets');
    expect(container.querySelectorAll('.mds-brief-tools > div')).toHaveLength(toolCounts().tools);
    expect(toolCounts()).toEqual({ tools: TOOLS.length, targets: gatewayTargets.length });
  });
});
