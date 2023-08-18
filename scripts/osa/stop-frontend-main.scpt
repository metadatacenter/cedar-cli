tell application "iTerm2"
  tell current window
      tell current session
          write text "kill $(cat ~/.cedar/pid-frontend-main.txt)"
      end tell
  end tell
end tell
