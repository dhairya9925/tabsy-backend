ALTER TABLE "contacts" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "friend_invites" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "shadow_profiles" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE POLICY "Users can view their own contacts" ON "contacts" AS PERMISSIVE FOR SELECT TO "authenticated" USING (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Users can insert their own contacts" ON "contacts" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Users can update their own contacts" ON "contacts" AS PERMISSIVE FOR UPDATE TO "authenticated" USING (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Users can delete their own contacts" ON "contacts" AS PERMISSIVE FOR DELETE TO "authenticated" USING (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Inviter can view their sent invites" ON "friend_invites" AS PERMISSIVE FOR SELECT TO "authenticated" USING (auth.uid() = inviter_user_id);--> statement-breakpoint
CREATE POLICY "Invitee can view their received invites" ON "friend_invites" AS PERMISSIVE FOR SELECT TO "authenticated" USING (auth.uid() = invitee_user_id);--> statement-breakpoint
CREATE POLICY "Users can insert invites they send" ON "friend_invites" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK (auth.uid() = inviter_user_id);--> statement-breakpoint
CREATE POLICY "Inviter can update their invites" ON "friend_invites" AS PERMISSIVE FOR UPDATE TO "authenticated" USING (auth.uid() = inviter_user_id);--> statement-breakpoint
CREATE POLICY "Invitee can update invites addressed to them" ON "friend_invites" AS PERMISSIVE FOR UPDATE TO "authenticated" USING (auth.uid() = invitee_user_id);--> statement-breakpoint
CREATE POLICY "Users can view their own shadow profiles" ON "shadow_profiles" AS PERMISSIVE FOR SELECT TO "authenticated" USING (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Users can insert their own shadow profiles" ON "shadow_profiles" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Users can update their own shadow profiles" ON "shadow_profiles" AS PERMISSIVE FOR UPDATE TO "authenticated" USING (auth.uid() = owner_user_id);--> statement-breakpoint
CREATE POLICY "Users can delete their own shadow profiles" ON "shadow_profiles" AS PERMISSIVE FOR DELETE TO "authenticated" USING (auth.uid() = owner_user_id);