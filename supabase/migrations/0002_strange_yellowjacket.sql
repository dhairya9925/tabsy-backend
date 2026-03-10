ALTER POLICY "Group members can view expense splits" ON "expense_splits" TO authenticated USING ((EXISTS ( SELECT 1
   FROM expenses e
  WHERE ((e.id = expense_splits.expense_id) AND (e.group_id IS NOT NULL) AND public.is_group_member(e.group_id, auth.uid())))));--> statement-breakpoint
ALTER POLICY "Group members can create group expenses" ON "expenses" TO authenticated WITH CHECK (((group_id IS NOT NULL) AND (user_id = auth.uid()) AND public.is_group_member(group_id, auth.uid())));--> statement-breakpoint
ALTER POLICY "Members can view group members" ON "group_members" TO authenticated USING (public.is_group_member(group_id, auth.uid()));--> statement-breakpoint
ALTER POLICY "Members can view their groups" ON "groups" TO authenticated USING (public.is_group_member(id, auth.uid()));