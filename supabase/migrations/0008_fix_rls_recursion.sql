CREATE OR REPLACE FUNCTION public.is_involved_in_expense(exp_id uuid, uid uuid)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM expenses WHERE id = exp_id AND user_id = uid
  ) OR EXISTS (
    SELECT 1 FROM expense_splits WHERE expense_id = exp_id AND user_id = uid
  );
$$;

DROP POLICY IF EXISTS "Users involved in splits can view non-group expenses" ON expenses;
CREATE POLICY "Users involved in splits can view non-group expenses" 
ON expenses AS PERMISSIVE FOR SELECT TO "authenticated" 
USING (group_id IS NULL AND public.is_involved_in_expense(id, auth.uid()));

DROP POLICY IF EXISTS "Users can view non-group expense splits" ON expense_splits;
CREATE POLICY "Users can view non-group expense splits" 
ON expense_splits AS PERMISSIVE FOR SELECT TO "authenticated" 
USING (
  EXISTS (
    SELECT 1 FROM expenses e
    WHERE e.id = expense_splits.expense_id
      AND e.group_id IS NULL
  )
  AND public.is_involved_in_expense(expense_id, auth.uid())
);
