CREATE TABLE "friends" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"friend_id" uuid NOT NULL,
	"status" text DEFAULT 'pending' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "friends_user_id_friend_id_key" UNIQUE("user_id","friend_id"),
	CONSTRAINT "friends_status_check" CHECK (status IN ('pending', 'accepted', 'rejected')),
	CONSTRAINT "friends_no_self_add_check" CHECK (user_id <> friend_id)
);
--> statement-breakpoint
ALTER TABLE "friends" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "contacts" DISABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "friend_invites" DISABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "shadow_profiles" DISABLE ROW LEVEL SECURITY;--> statement-breakpoint
DROP POLICY "Users can view their own contacts" ON "contacts" CASCADE;--> statement-breakpoint
DROP POLICY "Users can insert their own contacts" ON "contacts" CASCADE;--> statement-breakpoint
DROP POLICY "Users can update their own contacts" ON "contacts" CASCADE;--> statement-breakpoint
DROP POLICY "Users can delete their own contacts" ON "contacts" CASCADE;--> statement-breakpoint
DROP TABLE "contacts" CASCADE;--> statement-breakpoint
DROP POLICY "Inviter can view their sent invites" ON "friend_invites" CASCADE;--> statement-breakpoint
DROP POLICY "Invitee can view their received invites" ON "friend_invites" CASCADE;--> statement-breakpoint
DROP POLICY "Users can insert invites they send" ON "friend_invites" CASCADE;--> statement-breakpoint
DROP POLICY "Inviter can update their invites" ON "friend_invites" CASCADE;--> statement-breakpoint
DROP POLICY "Invitee can update invites addressed to them" ON "friend_invites" CASCADE;--> statement-breakpoint
DROP TABLE "friend_invites" CASCADE;--> statement-breakpoint
DROP POLICY "Users can view their own shadow profiles" ON "shadow_profiles" CASCADE;--> statement-breakpoint
DROP POLICY "Users can insert their own shadow profiles" ON "shadow_profiles" CASCADE;--> statement-breakpoint
DROP POLICY "Users can update their own shadow profiles" ON "shadow_profiles" CASCADE;--> statement-breakpoint
DROP POLICY "Users can delete their own shadow profiles" ON "shadow_profiles" CASCADE;--> statement-breakpoint
DROP TABLE "shadow_profiles" CASCADE;--> statement-breakpoint
ALTER TABLE "profiles" ADD COLUMN "is_shadow" boolean DEFAULT false NOT NULL;--> statement-breakpoint
ALTER TABLE "profiles" ADD COLUMN "shadow_created_by" uuid;--> statement-breakpoint
ALTER TABLE "profiles" ADD CONSTRAINT "profiles_shadow_created_by_profiles_user_id_fk" FOREIGN KEY ("shadow_created_by") REFERENCES "public"."profiles"("user_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE POLICY "Users can view shadow profiles they created" ON "profiles" AS PERMISSIVE FOR SELECT TO "authenticated" USING ((is_shadow = true AND shadow_created_by = auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can insert shadow profiles" ON "profiles" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK ((is_shadow = true AND shadow_created_by = auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can view their own friend records" ON "friends" AS PERMISSIVE FOR SELECT TO "authenticated" USING ((user_id = auth.uid() OR friend_id = auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can insert friend requests" ON "friends" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK ((user_id = auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can update friend requests sent to them" ON "friends" AS PERMISSIVE FOR UPDATE TO "authenticated" USING ((friend_id = auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can delete their own friend records" ON "friends" AS PERMISSIVE FOR DELETE TO "authenticated" USING ((user_id = auth.uid() OR friend_id = auth.uid()));