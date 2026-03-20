ALTER POLICY "Users can view non-group expense splits" ON "expense_splits" TO authenticated USING ((EXISTS (
		SELECT 1 FROM expenses e
		WHERE e.id = expense_splits.expense_id
		  AND e.group_id IS NULL
	  ) AND public.is_involved_in_expense(expense_id, auth.uid())));--> statement-breakpoint
ALTER POLICY "Users involved in splits can view non-group expenses" ON "expenses" TO authenticated USING ((group_id IS NULL AND public.is_involved_in_expense(id, auth.uid())));