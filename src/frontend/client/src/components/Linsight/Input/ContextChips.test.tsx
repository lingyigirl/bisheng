/**
 * F063 review (M1) — the org-KB tier marker must never become a chip.
 *
 * The marker is a state carrier whose `name` is empty (坑 13). It reaches this
 * component because `ExecutionFlow` falls back to the landing page's `'new'`
 * atom key whenever `linsight.session_id` is not yet populated, and the landing
 * page is where the tier is most often set — so "the two never share a key in
 * practice" (the T-17 audit comment) does not hold for that window. Rendering it
 * drew a blank chip and kept an otherwise empty chip row on screen.
 *
 * Case 2 is the positive control for case 1: a bare `expect(...).toBeNull()`
 * would also pass if the component rendered nothing for an unrelated reason.
 */

import { render, screen } from '@testing-library/react';
import { ContextChips } from './ContextChips';
import type { TaskModeKnowledgeItem } from '~/store/linsight';
import { ORG_SCOPE_MARKER_ID } from '~/utils/knowledgeScope';

const space: TaskModeKnowledgeItem = { id: '7', name: 'SPACE-7', type: 'space' };
const marker: TaskModeKnowledgeItem = { id: ORG_SCOPE_MARKER_ID, name: '', type: 'org' };

const noop = () => {};

const renderChips = (knowledge: TaskModeKnowledgeItem[]) =>
  render(
    <ContextChips
      skills={[]}
      knowledge={knowledge}
      attachmentFiles={[]}
      onRemoveSkill={noop}
      onRemoveKnowledge={noop}
      onRemoveFile={noop}
    />,
  );

describe('ContextChips — F063 tier marker (pitfall 13)', () => {
  it('renders nothing when the only entry is the tier marker', () => {
    renderChips([marker]);

    expect(screen.queryByLabelText('Remove')).toBeNull();
    expect(screen.queryByText('SPACE-7')).toBeNull();
  });

  it('renders the real entries and adds no blank chip when the marker rides along', () => {
    renderChips([space, marker]);

    expect(screen.getByText('SPACE-7')).toBeTruthy();
    // Exactly one removable chip: the marker would contribute a second, empty
    // one (its label is `item.name`, i.e. '').
    expect(screen.getAllByLabelText('Remove')).toHaveLength(1);
  });
});
