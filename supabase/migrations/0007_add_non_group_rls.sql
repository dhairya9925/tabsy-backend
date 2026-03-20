CREATE POLICY "Users can view non-group expense splits" ON "expense_splits" AS PERMISSIVE FOR SELECT TO "authenticated" USING ((EXISTS (
		SELECT 1 FROM expenses e
		WHERE e.id = expense_splits.expense_id
		  AND e.group_id IS NULL
		  AND (
			e.user_id = auth.uid() OR
			EXISTS (
			  SELECT 1 FROM expense_splits es2
			  WHERE es2.expense_id = e.id AND es2.user_id = auth.uid()
			)
		  )
	  )));--> statement-breakpoint
CREATE POLICY "Users involved in splits can view non-group expenses" ON "expenses" AS PERMISSIVE FOR SELECT TO "authenticated" USING ((group_id IS NULL AND EXISTS (SELECT 1 FROM expense_splits es WHERE es.expense_id = id AND es.user_id = auth.uid())));