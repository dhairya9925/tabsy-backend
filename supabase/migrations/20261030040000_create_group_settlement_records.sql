-- Create group_settlement_records table for group expense settlement audit trails
CREATE TABLE IF NOT EXISTS public.group_settlement_records (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  group_id UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
  from_user_id UUID NOT NULL,
  to_user_id UUID NOT NULL,
  amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
  note TEXT,
  settled_by UUID NOT NULL,
  settled_split_ids JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  editable_until TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '3 days'),
  is_disputed BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_group_settlement_records_group_id ON public.group_settlement_records(group_id);
CREATE INDEX IF NOT EXISTS idx_group_settlement_records_from_user ON public.group_settlement_records(from_user_id);
CREATE INDEX IF NOT EXISTS idx_group_settlement_records_to_user ON public.group_settlement_records(to_user_id);
CREATE INDEX IF NOT EXISTS idx_group_settlement_records_created_at ON public.group_settlement_records(created_at);

-- Enable RLS
ALTER TABLE public.group_settlement_records ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS "Group members can view settlement records" ON public.group_settlement_records;
DROP POLICY IF EXISTS "Group members can insert settlement records" ON public.group_settlement_records;
DROP POLICY IF EXISTS "Group members can update settlement records" ON public.group_settlement_records;
DROP POLICY IF EXISTS "Group members can delete settlement records" ON public.group_settlement_records;

-- Policy: Group members can view settlement records
CREATE POLICY "Group members can view settlement records"
  ON public.group_settlement_records FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_settlement_records.group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Policy: Group members can insert settlement records
CREATE POLICY "Group members can insert settlement records"
  ON public.group_settlement_records FOR INSERT
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Policy: Group members can update settlement records
CREATE POLICY "Group members can update settlement records"
  ON public.group_settlement_records FOR UPDATE
  USING (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Policy: Group members can delete settlement records
CREATE POLICY "Group members can delete settlement records"
  ON public.group_settlement_records FOR DELETE
  USING (
    EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = group_id
      AND gm.user_id = auth.uid()
    )
  );
