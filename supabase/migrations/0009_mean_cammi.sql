ALTER TABLE "expenses" ADD COLUMN "status" text DEFAULT 'submitted' NOT NULL;--> statement-breakpoint
ALTER TABLE "expenses" ADD COLUMN "receipt_url" text;--> statement-breakpoint
ALTER TABLE "groups" ADD COLUMN "sponsor_id" uuid;--> statement-breakpoint
ALTER TABLE "groups" ADD CONSTRAINT "groups_sponsor_id_profiles_user_id_fk" FOREIGN KEY ("sponsor_id") REFERENCES "public"."profiles"("user_id") ON DELETE no action ON UPDATE no action;