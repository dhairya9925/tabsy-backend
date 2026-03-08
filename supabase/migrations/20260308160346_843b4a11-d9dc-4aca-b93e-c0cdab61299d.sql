
-- Create expense_splits table to track how group expenses are divided
CREATE TABLE public.expense_splits (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  expense_id uuid NOT NULL REFERENCES public.expenses(id) ON DELETE CASCADE,
  user_id uuid NOT NULL,
  amount numeric NOT NULL,
  is_settled boolean NOT NULL DEFAULT false,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);

-- Enable RLS
ALTER TABLE public.expense_splits ENABLE ROW LEVEL SECURITY;

-- Members of the group can view splits for group expenses
CREATE POLICY "Group members can view expense splits"
  ON public.expense_splits FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.expenses e
      JOIN public.group_members gm ON gm.group_id = e.group_id
      WHERE e.id = expense_splits.expense_id
      AND gm.user_id = auth.uid()
    )
  );

-- Expense creator can insert splits
CREATE POLICY "Expense creator can insert splits"
  ON public.expense_splits FOR INSERT TO authenticated
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM public.expenses e
      WHERE e.id = expense_splits.expense_id
      AND e.user_id = auth.uid()
    )
  );

-- Expense creator can delete splits
CREATE POLICY "Expense creator can delete splits"
  ON public.expense_splits FOR DELETE TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.expenses e
      WHERE e.id = expense_splits.expense_id
      AND e.user_id = auth.uid()
    )
  );

-- Splits can be updated (for settling)
CREATE POLICY "Group members can update their splits"
  ON public.expense_splits FOR UPDATE TO authenticated
  USING (
    user_id = auth.uid()
    OR EXISTS (
      SELECT 1 FROM public.expenses e
      WHERE e.id = expense_splits.expense_id
      AND e.user_id = auth.uid()
    )
  );

-- Update expenses RLS to allow group members to view group expenses
CREATE POLICY "Group members can view group expenses"
  ON public.expenses FOR SELECT TO authenticated
  USING (
    group_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = expenses.group_id
      AND gm.user_id = auth.uid()
    )
  );

-- Allow group members to insert group expenses
CREATE POLICY "Group members can create group expenses"
  ON public.expenses FOR INSERT TO authenticated
  WITH CHECK (
    group_id IS NOT NULL
    AND user_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM public.group_members gm
      WHERE gm.group_id = expenses.group_id
      AND gm.user_id = auth.uid()
    )
  );
