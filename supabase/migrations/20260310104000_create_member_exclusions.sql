-- Track members excluded from general expenses for a given month
-- exclusion_type = 'partial' means they still pay rent but not general expenses

CREATE TABLE IF NOT EXISTS public.member_monthly_exclusions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  group_id UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
  user_id UUID NOT NULL,
  month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
  year INTEGER NOT NULL CHECK (year >= 2020),
  exclusion_type TEXT NOT NULL DEFAULT 'partial' CHECK (exclusion_type IN ('partial', 'full')),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (group_id, user_id, month, year)
);

-- Enable RLS
ALTER TABLE public.member_monthly_exclusions ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any (to avoid duplicates)
DROP POLICY IF EXISTS "Group members can view exclusions" ON public.member_monthly_exclusions;
DROP POLICY IF EXISTS "Group members can insert exclusions" ON public.member_monthly_exclusions;
DROP POLICY IF EXISTS "Group members can update exclusions" ON public.member_monthly_exclusions;
DROP POLICY IF EXISTS "Group members can delete exclusions" ON public.member_monthly_exclusions;

-- Policy: group members can view exclusions
CREATE POLICY "Group members can view exclusions"
  ON public.member_monthly_exclusions FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = member_monthly_exclusions.group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Policy: group members can insert exclusions
CREATE POLICY "Group members can insert exclusions"
  ON public.member_monthly_exclusions FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Policy: group members can update exclusions
CREATE POLICY "Group members can update exclusions"
  ON public.member_monthly_exclusions FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Policy: group members can delete exclusions
CREATE POLICY "Group members can delete exclusions"
  ON public.member_monthly_exclusions FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_id
      AND gm.user_id = auth.uid()
    )
  );
